import os
import json
import subprocess
from datetime import datetime, timezone, timedelta
import ccxt
import pandas as pd
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

exchange = ccxt.bingx()
MEMORY_FILE = "memory.json"

def send_error_to_telegram(error_text):
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
    try:
        subprocess.run(["git", "config", "--global", "user.name", "CryptoBot Action"], capture_output=True)
        subprocess.run(["git", "config", "--global", "user.email", "bot@action.com"], capture_output=True)
        subprocess.run(["git", "add", MEMORY_FILE], capture_output=True)
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if MEMORY_FILE in status.stdout:
            subprocess.run(["git", "commit", "-m", "Update bot memory [skip ci]"], capture_output=True)
            subprocess.run(["git", "push"], capture_output=True)
    except Exception as e:
        print(f"Предупреждение Git: {e}")

def get_best_liquid_coin(memory):
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
    current_price = float(df['close'].iloc[-1])
    sma10 = float(df['close'].rolling(10).mean().iloc[-1])
    is_long = current_price >= sma10
    
    if is_long:
        direction = "Long 🟢"
        sl = round(current_price * 0.985, 4)
        tp1 = round(current_price * 1.015, 4)
        tp2 = round(current_price * 1.032, 4)
        tp3 = round(current_price * 1.055, 4)
    else:
        direction = "Short 🔴"
        sl = round(current_price * 1.015, 4)
        tp1 = round(current_price * 0.985, 4)
        tp2 = round(current_price * 0.968, 4)
        tp3 = round(current_price * 0.945, 4)
        
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

def generate_tradingview_html_chart(df, symbol, levels):
    """Генерирует HTML с официальным движком TradingView Lightweight Charts"""
    chart_path = 'chart.png'
    
    # Форматируем свечи для JS-библиотеки TradingView
    candles_data = []
    for _, row in df.iterrows():
        # Время в секундах для Lightweight Charts
        time_sec = int(row['timestamp'] / 1000)
        candles_data.append({
            'time': time_sec,
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close'])
        })
        
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <script src="https://unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js"></script>
        <style>
            body {{
                background-color: #131722;
                margin: 0;
                font-family: -apple-system, BlinkMacSystemFont, sans-serif;
                color: #d1d4dc;
            }}
            #chart-container {{
                width: 1200px;
                height: 675px;
                position: relative;
            }}
            .watermark {{
                position: absolute;
                top: 20px;
                left: 20px;
                font-size: 20px;
                font-weight: bold;
                color: rgba(255, 255, 255, 0.3);
                z-index: 10;
                pointer-events: none;
            }}
        </style>
    </head>
    <body>
        <div class="watermark">BingX Pro Terminal: {symbol} ({levels['direction']})</div>
        <div id="chart-container"></div>
        <script>
            const container = document.getElementById('chart-container');
            const chart = LightweightCharts.createChart(container, {{
                width: container.clientWidth,
                height: container.clientHeight,
                layout: {{
                    background: {{ color: '#131722' }},
                    textColor: '#d1d4dc',
                }},
                grid: {{
                    vertLines: {{ color: '#2a2e39' }},
                    horzLines: {{ color: '#2a2e39' }},
                }},
                timeScale: {{
                    timeVisible: true,
                    secondsVisible: false,
                }}
            }});

            const candlestickSeries = chart.addCandlestickSeries({{
                upColor: '#26a69a',
                downColor: '#ef5350',
                borderVisible: false,
                wickUpColor: '#26a69a',
                wickDownColor: '#ef5350',
            }});

            const candleData = {json.dumps(candles_data)};
            candlestickSeries.setData(candleData);

            // Функция добавления линий уровней
            function addLevelLine(price, color, title) {{
                const lineSeries = chart.addLineSeries({{
                    color: color,
                    lineWidth: 2,
                    lineStyle: 2, // Пунктир
                    crosshairMarkerVisible: false,
                    lastValueVisible: true,
                    title: title,
                }});
                
                // Растягиваем линию на всю историю графика
                const lineData = candleData.map(c => ({{ time: c.time, value: price }}));
                lineSeries.setData(lineData);
            }}

            addLevelLine({levels['entry']}, '#2962FF', 'Entry');
            addLevelLine({levels['tp1']}, '#26a69a', 'TP1');
            addLevelLine({levels['tp2']}, '#26a69a', 'TP2');
            addLevelLine({levels['tp3']}, '#26a69a', 'TP3');
            addLevelLine({levels['sl']}, '#ef5350', 'Stop Loss');

            chart.timeScale().fitContent();
        </script>
    </body>
    </html>
    """
    
    html_file = 'chart.html'
    with open(html_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
        
    # Конвертируем HTML в картинку через инструмент командной строки (например, wkhtmltoimage или playright/chromium, если доступно, либо возвращаем html)
    # На GitHub Actions проще всего использовать легкий python-конвертер или playwright
    print("HTML-граф TradingView успешно сформирован.")
    return html_file

def ask_openrouter_human_style(symbol, levels):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
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
    data = {
        "model": "openrouter/free",
        "messages": [{"role": "user", "content": prompt}]
    }
    response = requests.post(url, headers=headers, json=data)
    if response.status_code != 200:
        raise Exception(f"Ошибка API OpenRouter: {response.text}")
    res_json = response.json()
    return res_json['choices'][0]['message']['content']

if __name__ == "__main__":
    try:
        memory = load_memory()
        print("Сканируем ликвидный рынок BingX...")
        symbol = get_best_liquid_coin(memory)
        print(f"Выбрана монета: {symbol}")
        
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=50)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        levels = calculate_realistic_levels(df)
        
        print("Генерируем интерактивный график TradingView...")
        chart_file = generate_tradingview_html_chart(df, symbol, levels)
        
        print("Генерация аналитики через OpenRouter...")
        analysis_text = ask_openrouter_human_style(symbol, levels)
        
        memory["last_coin"] = symbol
        save_memory(memory)
        git_commit_memory()
        
        print(f"Аналитика и разметка по {symbol} успешно подготовлены!")
    except Exception as e:
        error_msg = str(e)
        print(f"Критическая ошибка: {error_msg}")
        send_error_to_telegram(error_msg)
