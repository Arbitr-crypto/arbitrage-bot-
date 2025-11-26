import ccxt
import pandas as pd
import requests
import time
from datetime import datetime

# ------------------------------
# Telegram
# ------------------------------
TOKEN = "ВАШ_ТЕЛЕГРАМ_ТОКЕН"
CHAT_ID = "ВАШ_CHAT_ID"

def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text}
    try:
        r = requests.post(url, data=payload)
        return r.json()
    except Exception as e:
        print("Ошибка Telegram:", e)

# ------------------------------
# Биржи (публичный доступ)
# ------------------------------
exchanges = {
    'kucoin': ccxt.kucoin(),
    'bitrue': ccxt.bitrue(),
    'bitmart': ccxt.bitmart(),
    'gateio': ccxt.gateio(),
    'poloniex': ccxt.poloniex(),
    'htx': ccxt.htx(),       # или 'huobi' если 'htx' не работает
    'bybit': ccxt.bybit()
}

# ------------------------------
# Настройки
# ------------------------------
SPREAD_THRESHOLD = 0.02  # 2%
MIN_VOLUME = 200          # минимальный объём в стакане
CHECK_INTERVAL = 30       # интервал проверки, сек
MAX_COINS = 150            # для теста первые 50 монет

# ------------------------------
# Загружаем монеты с бирж
# ------------------------------
print("📌 Загружаю торговые пары...")
exchange_symbols = {}
for ex_id, ex in exchanges.items():
    try:
        markets = ex.load_markets()
        exchange_symbols[ex_id] = list(markets.keys())
        print(f"✔ {ex_id.upper()} загружено {len(exchange_symbols[ex_id])} монет")
    except Exception as e:
        exchange_symbols[ex_id] = []
        print(f"❌ Ошибка {ex_id}: {e}")

# Находим общие монеты для всех бирж
common_symbols = set(exchange_symbols['kucoin'])
for ex_id in exchanges.keys():
    common_symbols = common_symbols.intersection(exchange_symbols[ex_id])
common_symbols = sorted(list(common_symbols))[:MAX_COINS]

print("\n==============================")
print(f"🔍 Выбрано {len(common_symbols)} монет для проверки.\n")

# ------------------------------
# Функция проверки объёма в стакане
# ------------------------------
def get_orderbook_volume(ex, symbol):
    try:
        ob = ex.fetch_order_book(symbol)
        bid_volume = sum([p*a for p,a in ob['bids'][:5]])
        ask_volume = sum([p*a for p,a in ob['asks'][:5]])
        return max(bid_volume, ask_volume)
    except:
        return 0

# ------------------------------
# Основной цикл поиска арбитража
# ------------------------------
print("📌 Старт бота...")
while True:
    print(f"\n{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    for symbol in common_symbols:
        volumes = [get_orderbook_volume(exchanges[ex_id], symbol) for ex_id in exchanges]
        if any(v < MIN_VOLUME for v in volumes):
            continue
        
        prices = {}
        for ex_id, ex in exchanges.items():
            try:
                ticker = ex.fetch_ticker(symbol)
                prices[ex_id] = ticker.get('last') or ticker.get('close') or ticker.get('bid')
            except:
                prices[ex_id] = None
        prices = {k:v for k,v in prices.items() if v is not None}
        if len(prices) < 2:
            continue

        min_ex = min(prices, key=prices.get)
        max_ex = max(prices, key=prices.get)
        min_price = prices[min_ex]
        max_price = prices[max_ex]
        spread = (max_price - min_price) / min_price

        if spread >= SPREAD_THRESHOLD:
            network_info = "Сеть: TBD"  # placeholder, позже можно подключить API бирж
            msg = (
                f"🔥 Арбитраж! {symbol}\n"
                f"Купить: {min_ex} → {min_price:.2f}\n"
                f"Продать: {max_ex} → {max_price:.2f}\n"
                f"СПРЕД: {spread*100:.2f}%\n"
                f"Объём: {max(volumes):.2f} USD\n"
                f"{network_info}"
            )
            print(msg)
            send_message(msg)
        else:
            print(f"{symbol}: spread={spread*100:.2f}% — ниже порога")
    time.sleep(CHECK_INTERVAL)

