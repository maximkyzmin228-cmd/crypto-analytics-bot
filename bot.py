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
COOLDOWN_HOURS = 0  # Для тестов кулдаун отключен

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
    """Безопасное автосохранение памяти через Git без падения скрипта"""
    try:
        subprocess.run(["git", "config", "--global", "user.name", "CryptoBot Action"], capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@action.com"], capture_output=True)
        subprocess.run(["git", "add", MEMORY_FILE], capture_output=True)
        
        # Проверяем, есть ли изменения для коммита
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if MEMORY_FILE in status.stdout:
            subprocess.run(["git", "commit", "-m", "Update bot memory [skip ci]"], capture_output=True)
            subprocess.run(["git", "push"], capture_output=True)
            print("Память успешно обновлена в репозитории.")
        else:
            print("Изменений в памяти нет, пропускаем git push.")
    except Exception as e:
        print(f"Предупреждение Git (не влияет на отправку в Telegram): {e}")

def get_best_liquid_coin(memory):
    """Надежный поиск монеты с объемом >$50M"""
    try:
        tickers = exchange.fetch_tickers()
        candidates = []
        
        for symbol, ticker in tickers.items():
            if not symbol.endswith('/USDT'):
                continue
            quote_volume = ticker.get('quoteVolume')
            if not quote_volume and ticker.get('close') and ticker.get('baseVolume'):
                quote_volume = ticker['close'] * ticker['baseVolume']
            quote_volume = quote_volume or 0
            
            if quote_volume >= 50_000_000:
                change = ticker.get('percentage') or 0
                candidates.append({
                    'symbol': symbol,
                    'volume': quote_volume,
                    'change': abs(change)
                })
                
        candidates.sort(key=lambda x: x['change'], reverse=True)
        
        last_coin = memory.get("last_coin", "")
        for item in candidates:
            sym = item['symbol']
            if sym != last_coin or len(candidates) == 1:
                return sym
                
        return candidates[0]['symbol'] if candidates else "BTC/USDT"
    except Exception as e:
        send_error_to_telegram(f"Не удалось отфильтровать рынок BingX: {e}")
        return "BTC/USDT"

def calculate_realistic_levels(df):
    """Точный расчет уровней в процентах от реальной цены (для плеча x7-x10)"""
    current_price = float(df['close'].iloc[-1])
    sma10 = float(df['close'].rolling(10).mean().iloc[-1])
    
    is_long = current_price >= sma10
    
    if is_long:
        direction = "Long 🟢"
        sl = round(current_price * 0.985, 4)   # Стоп-лосс -1.5%
        tp1 = round(current_price * 1.015, 4)  # TP1 +1.5%
        tp2 = round(current_price * 1.032, 4)  # TP2 +3.2%
        tp3 = round(current_price * 1.055, 4)  # TP3 +5.5%
    else:
        direction = "Short 🔴"
        sl = round(current_price * 1.015, 4)   # Стоп-лосс +1.5%
        tp1 = round(current_price * 0.985, 4)  # TP1 -1.5%
        tp2 = round(current_price * 0.968, 4)  # TP2 -3.2%
        tp3 = round(current_price * 0.945, 4)  # TP3 -5.5%
        
    recent_high = float(df['high'].iloc[-15:].max())
    recent_low = float(df['low'].iloc[-15:].min())
    
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

def generate_terminal_chart(df, symbol, levels):
    """Генерация профессионального свечного графика в стиле TradingView"""
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
        '#2962FF',  # Вход - Синий
        '#26a69a', '#26a69a', '#26a69a',  # Тейки - Зеленые
        '#ef5350',  # Стоп - Красный
        '#ff9800', '#ffeb3b'  # Поддержка / Сопротивление
    ]
    
    chart_path = 'chart.png'
    mpf.plot(
        chart_df,
        type='candle',
        style=s,
        volume=True,
        hlines=dict(hlines=hlines_vals, colors=hlines_colors, linestyle='--', linewidths=1.2),
        title=f"\nBingX Pro Terminal: {symbol} ({levels['direction']})",
        savefig=dict(fname=chart_path, dpi=300, bbox_inches='tight')
    )
    return chart_path

def ask_gemini_human_style(symbol, levels):
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
    """Отправка графика и текста в ЛС Telegram"""
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
        
        print("Сканируем ликвидный рынок BingX...")
        symbol = get_best_liquid_coin(memory)
        print(f"Выбрана монета: {symbol}")
        
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=40)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        levels = calculate_realistic_levels(df)
        
        print("Генерируем профессиональный терминальный график...")
        chart_file = generate_terminal_chart(df, symbol, levels)
        
        analysis_text = ask_gemini_human_style(symbol, levels)
        
        send_to_telegram(analysis_text, chart_file)
        
        memory["last_coin"] = symbol
        save_memory(memory)
        git_commit_memory()
        
        print(f"Сетап по {symbol} успешно отправлен в ЛС!")
    except Exception as e:
        error_msg = str(e)
        print(f"Критическая ошибка: {error_msg}")
        send_error_to_telegram(error_msg)
