import os
import json
import time
import subprocess
from datetime import datetime, timezone, timedelta
import ccxt
import google.generativeai as genai
import mplfinance as mpf
import pandas as pd
import requests

# Загружаем ключи из секретов GitHub
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Настройка Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-3.8-flash')

# Подключение к BingX
exchange = ccxt.bingx()

MEMORY_FILE = "memory.json"
COOLDOWN_HOURS = 0  # На время тестов убираем кулдаун между монетами

def send_error_to_telegram(error_text):
    """Отправка уведомления о сбое в ЛС"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": f"⚠️ СИСТЕМНЫЙ СБОЙ БОТА:\n{error_text}"}
        requests.post(url, data=data)
    except Exception:
        pass

def load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_memory(memory):
    with open(MEMORY_FILE, 'w') as f:
        json.dump(memory, f, indent=4)

def git_commit_memory():
    """Автосохранение памяти в репозиторий GitHub"""
    try:
        subprocess.run(["git", "config", "--global", "user.name", "CryptoBot Action"], check=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@action.com"], check=True)
        subprocess.run(["git", "add", MEMORY_FILE], check=True)
        subprocess.run(["git", "commit", "-m", "Update bot memory [skip ci]"], check=True)
        subprocess.run(["git", "push"], check=True)
    except Exception as e:
        print(f"Ошибка обновления памяти через Git: {e}")

def check_time_and_limits(memory):
    """РЕЖИМ ТЕСТОВ: лимиты временно отключены, бот работает всегда по клику"""
    print("🛠 Режим тестов: ограничения по времени и лимитам отключены.")
    return True

def get_best_liquid_coin(memory):
    """Динамический поиск монеты с объемом >$50M"""
    try:
        tickers = exchange.fetch_tickers()
        candidates = []
        
        for symbol, ticker in tickers.items():
            if not symbol.endswith('/USDT'):
                continue
            quote_volume = ticker.get('quoteVolume') or 0
            
            if quote_volume >= 50_000_000:
                change = ticker.get('percentage') or 0
                candidates.append({
                    'symbol': symbol,
                    'volume': quote_volume,
                    'change': abs(change)
                })
                
        candidates.sort(key=lambda x: x['change'], reverse=True)
        return candidates[0]['symbol'] if candidates else "BTC/USDT"
    except Exception as e:
        send_error_to_telegram(f"Не удалось отфильтровать рынок BingX: {e}")
        return "BTC/USDT"

def calculate_clean_trade_levels(df):
    """Чистый математический расчет уровней относительно реальной цены"""
    current_price = float(df['close'].iloc[-1])
    ma20 = float(df['close'].rolling(20).mean().iloc[-1])
    
    is_long = current_price >= ma20
    
    recent_high = float(df['high'].iloc[-20:].max())
    recent_low = float(df['low'].iloc[-20:].min())
    
    if is_long:
        direction = "Long 🟢"
        sl = round(min(current_price * 0.985, recent_low * 0.995), 4)
        risk = current_price - sl
        tp1 = round(current_price + risk * 1.5, 4)
        tp2 = round(current_price + risk * 2.8, 4)
        tp3 = round(current_price + risk * 4.5, 4)
    else:
        direction = "Short 🔴"
        sl = round(max(current_price * 1.015, recent_high * 1.005), 4)
        risk = sl - current_price
        tp1 = round(current_price - risk * 1.5, 4)
        tp2 = round(current_price - risk * 2.8, 4)
        tp3 = round(current_price - risk * 4.5, 4)
        
    return {
        'direction': direction,
        'entry': round(current_price, 4),
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'sl': sl,
        'support': round(recent_low, 4),
        'resistance': round(recent_high, 4)
    }

def generate_tradingview_style_chart(df, symbol, levels):
    """Генерация графика в стиле TradingView"""
    chart_df = df.copy()
    chart_df['timestamp'] = pd.to_datetime(chart_df['timestamp'], unit='ms')
    chart_df.set_index('timestamp', inplace=True)
    chart_df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']

    mc = mpf.make_marketcolors(
        up='#26a69a', down='#ef5350',
        wick={'up':'#26a69a', 'down':'#ef5350'},
        volume={'up':'#26a69a55', 'down':'#ef535055'},
        edge='inherit'
    )
    s = mpf.make_mpf_style(
        marketcolors=mc, 
        facecolor='#131722', 
        figcolor='#1e222d', 
        gridcolor='#2a2e39',
        edgecolor='#363a45',
        y_on_right=True
    )

    hlines_vals = [
        levels['entry'], 
        levels['tp1'], 
        levels['tp2'], 
        levels['tp3'], 
        levels['sl'],
        levels['support'],
        levels['resistance']
    ]
    hlines_colors = [
        '#2962FF',  # Вход
        '#26a69a', '#26a69a', '#26a69a',  # Тейки
        '#ef5350',  # Стоп
        '#ff9800', '#ffeb3b'  # Поддержка / Сопротивление
    ]
    
    chart_path = 'chart.png'
    mpf.plot(
        chart_df,
        type='candle',
        style=s,
        volume=True,
        hlines=dict(hlines=hlines_vals, colors=hlines_colors, linestyle='--', linewidths=1.0),
        title=f"\nBingX Pro Chart: {symbol} ({levels['direction']})",
        savefig=dict(fname=chart_path, dpi=300, bbox_inches='tight')
    )
    return chart_path

def ask_gemini_human_style(symbol, levels, df):
    """Генерация живого трейдерского поста"""
    prompt = f"""
    Ты опытный криптотрейдер. Напиши живой, пробивной торговый пост для Telegram по монете {symbol}.
    ОБЯЗАТЕЛЬНО ИСПОЛЬЗУЙ ИХ БЕЗ ИЗМЕНЕНИЙ В ТЕКСТЕ:
    - Направление: {levels['direction']}
    - Зона поддержки: {levels['support']} | Сопротивление: {levels['resistance']}
    - Точка входа: {levels['entry']}
    - TP1: {levels['tp1']} (фиксация 40%, перевод в безубыток)
    - TP2: {levels['tp2']} (фиксация 40%)
    - TP3: {levels['tp3']} (остаток по тренду)
    - Stop Loss: {levels['sl']}

    Стиль написания:
    1. Живой сленг трейдера, уместные стикеры и эмодзи 📉📈🚀. Без нейросетевого пафоса.
    2. Кратко объясни логику по поведению цены у указанных уровней поддержки/сопротивления.
    3. Укажи риск-менеджмент: плечо х7–х10, короткий технический стоп.
    """
    response = model.generate_content(prompt)
    return response.text

def send_to_telegram(text, image_path):
    """Отправка графика и текста в ЛС Telegram без разметки во избежание сбоев"""
    url_photo = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(image_path, 'rb') as photo:
        files = {'photo': photo}
        data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': text[:1024]}
        response = requests.post(url_photo, data=data, files=files)
        if response.status_code != 200:
            send_error_to_telegram(f"Ошибка отправки в Telegram: {response.text}")

if __name__ == "__main__":
    try:
        memory = load_memory()
        
        if not check_time_and_limits(memory):
            exit(0)
            
        print("Сканируем рынок BingX и рассчитываем чистые уровни...")
        symbol = get_best_liquid_coin(memory)
        
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=40)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        levels = calculate_clean_trade_levels(df)
        chart_file = generate_tradingview_style_chart(df, symbol, levels)
        analysis_text = ask_gemini_human_style(symbol, levels, df)
        
        send_to_telegram(analysis_text, chart_file)
        
        memory["last_coin"] = symbol
        save_memory(memory)
        git_commit_memory()
        
        print(f"Сетап по {symbol} успешно отправлен в ЛС!")
    except Exception as e:
        error_msg = str(e)
        print(f"Критическая ошибка: {error_msg}")
        send_error_to_telegram(error_msg)
