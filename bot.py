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
COOLDOWN_HOURS = 8  # Пауза для монеты перед повторным сигналом

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
    """Автосохранение памяти и лимитов в репозиторий GitHub"""
    try:
        subprocess.run(["git", "config", "--global", "user.name", "CryptoBot Action"], check=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@action.com"], check=True)
        subprocess.run(["git", "add", MEMORY_FILE], check=True)
        subprocess.run(["git", "commit", "-m", "Update bot memory & limits [skip ci]"], check=True)
        subprocess.run(["git", "push"], check=True)
    except Exception as e:
        print(f"Ошибка обновления памяти через Git: {e}")

def check_time_and_limits(memory):
    """Проверка лимита (макс 3 поста в день) и временного окна (10:30 - 18:00 МСК)"""
    msk_tz = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_tz)
    
    current_date_str = now_msk.strftime("%Y-%m-%d")
    current_hour = now_msk.hour
    current_minute = now_msk.minute
    
    current_total_minutes = current_hour * 60 + current_minute
    start_limit = 10 * 60 + 30  # 10:30 МСК
    end_limit = 18 * 60         # 18:00 МСК
    
    if not (start_limit <= current_total_minutes <= end_limit):
        print(f"⏰ Сейчас {now_msk.strftime('%H:%M')} МСК. Вне рабочего диапазона (10:30 - 18:00). Пропускаем.")
        return False

    if memory.get("last_date") != current_date_str:
        memory["last_date"] = current_date_str
        memory["posts_today"] = 0
        save_memory(memory)
        
    if memory.get("posts_today", 0) >= 3:
        print(f"🛑 Лимит 3 поста на сегодня ({current_date_str}) уже исчерпан.")
        return False
        
    return True

def get_best_liquid_coin(memory):
    """Динамический поиск монеты с объемом >$50M с защитой от повторов"""
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
        current_time = time.time()
        coin_cooldowns = memory.get("coin_cooldowns", {})
        last_coin = memory.get("last_coin", "")
        
        for item in candidates:
            sym = item['symbol']
            if sym == last_coin and len(candidates) > 1:
                continue
            last_signal_time = coin_cooldowns.get(sym, 0)
            if current_time - last_signal_time > COOLDOWN_HOURS * 3600:
                return sym
                
        return candidates[0]['symbol'] if candidates else "BTC/USDT"
    except Exception as e:
        send_error_to_telegram(f"Не удалось отфильтровать рынок BingX: {e}")
        return "BTC/USDT"

def calculate_trade_levels_and_patterns(df):
    """Расчет уровней сделки и ключевых зон поддержки/сопротивления"""
    current_price = df['close'].iloc[-1]
    
    recent_high = df['high'].iloc[-20:].max()
    recent_low = df['low'].iloc[-20:].min()
    mid_resistance = df['high'].iloc[-20:-5].max()
    mid_support = df['low'].iloc[-20:-5].min()
    
    mid_range = (recent_high + recent_low) / 2
    is_long = current_price <= mid_range
    
    if is_long:
        direction = "Long 🟢"
        sl = round(recent_low * 0.993, 4)
        risk = current_price - sl
        tp1 = round(current_price + risk * 1.5, 4)
        tp2 = round(current_price + risk * 2.5, 4)
        tp3 = round(current_price + risk * 4.0, 4)
    else:
        direction = "Short 🔴"
        sl = round(recent_high * 1.007, 4)
        risk = sl - current_price
        tp1 = round(current_price - risk * 1.5, 4)
        tp2 = round(current_price - risk * 2.5, 4)
        tp3 = round(current_price - risk * 4.0, 4)
        
    return {
        'direction': direction,
        'entry': current_price,
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'sl': sl,
        'support': mid_support,
        'resistance': mid_resistance
    }

def generate_pro_chart_with_patterns(df, symbol, levels):
    """Генерация свечного графика с линиями уровней и паттернов"""
    chart_df = df.copy()
    chart_df['timestamp'] = pd.to_datetime(chart_df['timestamp'], unit='ms')
    chart_df.set_index('timestamp', inplace=True)
    chart_df.columns = ['Open', 'High', 'Low', 'Close', 'Volume']

    # Профессиональная темная тема
    mc = mpf.make_marketcolors(
        up='#00E676', down='#FF5252',
        wick={'up':'#00E676', 'down':'#FF5252'},
        volume={'up':'#00E67655', 'down':'#FF525255'},
        edge='inherit'
    )
    s = mpf.make_mpf_style(marketcolors=mc, facecolor='#131722', figcolor='#181c25', gridcolor='#2a2e39')

    # Линии на графике: Вход (синий), Тейки (зеленые), Стоп (красный), Поддержка/Сопротивление (желтый/оранжевый)
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
        '#2962FF', 
        '#00E676', '#00E676', '#00E676', 
        '#FF5252', 
        '#FFD700', '#FFA500'
    ]
    
    chart_path = 'chart.png'
    mpf.plot(
        chart_df,
        type='candle',
        style=s,
        volume=True,
        hlines=dict(hlines=hlines_vals, colors=hlines_colors, linestyle='--', linewidths=1.2),
        title=f"\nBingX Pattern Analysis: {symbol} ({levels['direction']})",
        savefig=dict(fname=chart_path, dpi=300, bbox_inches='tight')
    )
    return chart_path

def ask_gemini_human_style(symbol, levels, df):
    """Генерация живого трейдерского поста под реальные уровни"""
    prompt = f"""
    Ты опытный криптотрейдер. Напиши живой, пробивной торговый пост для Telegram по монете {symbol}.
    ОБЯЗАТЕЛЬНО ИСПОЛЬЗУЙ ЭТИ ТОЧНЫЕ ЦИФРЫ В ТЕКСТЕ:
    - Направление: {levels['direction']}
    - Уровень поддержки: {levels['support']} | Сопротивление: {levels['resistance']}
    - Точка входа: {levels['entry']}
    - TP1: {levels['tp1']} (фиксация 40%, перевод в безубыток)
    - TP2: {levels['tp2']} (фиксация 40%)
    - TP3: {levels['tp3']} (остаток по тренду)
    - Stop Loss: {levels['sl']}

    Стиль написания:
    1. Живой сленг трейдера, уместные стикеры и эмодзи 📉📈🚀. Без нейросетевого пафоса.
    2. Кратко объясни логику по паттерну и поведению цены у уровней поддержки/сопротивления.
    3. Укажи риск-менеджмент: плечо х7–х10, короткий технический стоп.
    """
    response = model.generate_content(prompt)
    return response.text

def send_to_telegram(text, image_path):
    """Отправка графика и текста в ЛС Telegram"""
    url_photo = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    with open(image_path, 'rb') as photo:
        files = {'photo': photo}
        data = {'chat_id': TELEGRAM_CHAT_ID, 'caption': text[:1024], 'parse_mode': 'Markdown'}
        response = requests.post(url_photo, data=data, files=files)
        if response.status_code != 200:
            send_error_to_telegram(f"Ошибка отправки в Telegram: {response.text}")

if __name__ == "__main__":
    try:
        memory = load_memory()
        
        if not check_time_and_limits(memory):
            exit(0)
            
        print("Сканируем рынок BingX, находим паттерны и уровни...")
        symbol = get_best_liquid_coin(memory)
        
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=40)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        levels = calculate_trade_levels_and_patterns(df)
        chart_file = generate_pro_chart_with_patterns(df, symbol, levels)
        analysis_text = ask_gemini_human_style(symbol, levels, df)
        
        send_to_telegram(analysis_text, chart_file)
        
        # Обновляем память
        memory["posts_today"] = memory.get("posts_today", 0) + 1
        memory["last_coin"] = symbol
        if "coin_cooldowns" not in memory:
            memory["coin_cooldowns"] = {}
        memory["coin_cooldowns"][symbol] = time.time()
        
        save_memory(memory)
        git_commit_memory()
        
        print(f"Сетап по {symbol} отправлен в ЛС! Постов за сегодня: {memory['posts_today']}")
    except Exception as e:
        error_msg = str(e)
        print(f"Критическая ошибка: {error_msg}")
        send_error_to_telegram(error_msg)
