import os
import json
import time
import subprocess
from datetime import datetime, timezone, timedelta
import ccxt
import google.generativeai as genai
import matplotlib.pyplot as plt
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
    # Московское время (UTC +3)
    msk_tz = timezone(timedelta(hours=3))
    now_msk = datetime.now(msk_tz)
    
    current_date_str = now_msk.strftime("%Y-%m-%d")
    current_hour = now_msk.hour
    current_minute = now_msk.minute
    
    # Переводим текущее время в минуты с начала дня для точной проверки
    current_total_minutes = current_hour * 60 + current_minute
    start_limit = 10 * 60 + 30  # 10:30 МСК
    end_limit = 18 * 60         # 18:00 МСК
    
    if not (start_limit <= current_total_minutes <= end_limit):
        print(f"⏰ Сейчас {now_msk.strftime('%H:%M')} МСК. Вне рабочего диапазона (10:30 - 18:00). Пропускаем.")
        return False

    # Проверяем количество постов за сегодня
    if memory.get("last_date") != current_date_str:
        memory["last_date"] = current_date_str
        memory["posts_today"] = 0
        save_memory(memory)
        
    if memory.get("posts_today", 0) >= 3:
        print(f"🛑 Лимит 3 поста на сегодня ({current_date_str}) уже исчерпан.")
        return False
        
    return True

def get_best_liquid_coin(memory):
    """Динамический поиск лучшей монеты (объем > $50M, без мусора) с учетом кулдауна"""
    try:
        tickers = exchange.fetch_tickers()
        candidates = []
        
        for symbol, ticker in tickers.items():
            if not symbol.endswith('/USDT'):
                continue
            quote_volume = ticker.get('quoteVolume') or 0
            
            # Фильтр ликвидности: объем торгов строго от $50 млн
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
        
        for item in candidates:
            sym = item['symbol']
            last_signal_time = coin_cooldowns.get(sym, 0)
            if current_time - last_signal_time > COOLDOWN_HOURS * 3600:
                return sym
                
        return candidates[0]['symbol'] if candidates else "BTC/USDT"
    except Exception as e:
        send_error_to_telegram(f"Не удалось отфильтровать рынок BingX: {e}")
        return "BTC/USDT"

def generate_chart(df, symbol):
    """Автоматическая отрисовка графика"""
    plt.figure(figsize=(10, 5))
    plt.plot(df['close'], label='Price', color='#2962FF', linewidth=2)
    plt.title(f'BingX Live Monitor: {symbol}', fontsize=14, color='white')
    plt.grid(True, linestyle='--', alpha=0.3)
    plt.gca().set_facecolor('#131722')
    plt.gcf().patch.set_facecolor('#181c25')
    plt.tick_params(colors='white')
    plt.tight_layout()
    chart_path = 'chart.png'
    plt.savefig(chart_path, facecolor=plt.gcf().get_facecolor(), edgecolor='none')
    plt.close()
    return chart_path

def ask_gemini_human_style(symbol, price, df):
    """Генерация живого трейдерского поста через Gemini"""
    prompt = f"""
    Ты опытный криптотрейдер. Напиши живой, пробивной пост-аналитику для Telegram по монете {symbol} (текущая цена: {price}).
    Без канцелярщины и нейросетевых штампов. Используй разговорный язык трейдера, уместные стикеры и эмодзи.
    
    Структура поста:
    1. Название монеты и направление (Long/Short) с эмодзи.
    2. Живой контекст ситуации на рынке и логика входа (почему так происходит).
    3. Торговые цели: TP1, TP2, TP3 (с процентами фиксации и переводом в безубыток на TP1).
    4. Стоп-лосс (короткий и логичный с точки зрения техники).
    5. Риск-менеджмент и обоснованное плечо (например, x7-x10, так как стоп короткий).
    """
    response = model.generate_content(prompt)
    return response.text

def send_to_telegram(text, image_path):
    """Отправка связки (график + текст) в личные сообщения Telegram"""
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
        
        # Проверяем лимиты и время МСК
        if not check_time_and_limits(memory):
            exit(0)
            
        print("Запуск сканирования рынка BingX...")
        symbol = get_best_liquid_coin(memory)
        
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=50)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        current_price = df['close'].iloc[-1]
        
        chart_file = generate_chart(df, symbol)
        analysis_text = ask_gemini_human_style(symbol, current_price, df)
        
        send_to_telegram(analysis_text, chart_file)
        
        # Обновляем память (увеличиваем счетчик постов за день и ставим кулдаун на монету)
        memory["posts_today"] = memory.get("posts_today", 0) + 1
        if "coin_cooldowns" not in memory:
            memory["coin_cooldowns"] = {}
        memory["coin_cooldowns"][symbol] = time.time()
        
        save_memory(memory)
        git_commit_memory()
        
        print(f"Сигнал по {symbol} успешно отправлен в ЛС! Постов за сегодня: {memory['posts_today']}")
    except Exception as e:
        error_msg = str(e)
        print(f"Критическая ошибка: {error_msg}")
        send_error_to_telegram(error_msg)
