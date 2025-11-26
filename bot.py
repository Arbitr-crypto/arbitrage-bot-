import ccxt
import pandas as pd
import requests
import time
from datetime import datetime, timezone

# ------------------------------
# Telegram
# ------------------------------
TOKEN = "8546366016:AAEWSe8vsdlBhyboZzOgcPb8h9cDSj09A80"
CHAT_ID = "6590452577"

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
    'huobi': ccxt.huobi(),     # вместо htx
    # 'bybit': ccxt.bybit(),    # отключено, т.к. 403 на Railway
}

# ------------------------------
# Настройки
# ------------------------------
SPREAD_THRESHOLD = 0.02   # 2%
MIN_VOLUME = 200
CHECK_INTERVAL = 60
MAX_COINS = 150

# ------------------------------
# Загружаем монеты с бирж
# ------------------------------
print("📌 Загружаю торговые пары...")
exchange_symbols = {}
working_exchanges = {}

for ex_id, ex in exchanges.items():
    try:
        markets = ex.load_markets()
        symbols = list(markets.keys())
        if len(symbols) == 0:
            raise Exception("нет монет")
        exchange_symbols[ex_id] = symbols
        working_exchanges[ex_id] = ex
        print(f"✔️ {ex_id.upper()} загружено {len(symbols)} монет")
    except Exception as e:
        print(f"❌ Биржа отключена {ex_id}: {e}")

# ------------------------------
# Пересечение монет
# ------------------------------
if len(working_exchanges) < 2:
    print("❌ Ошибка: недостаточно рабочих бирж.")
    exit()

common_symbols = set(exchange_symbols[list(working_exchanges.keys())[0]])

for ex_id in working_exchanges:
    common_symbols = common_symbols.intersection(exchange_symbols[ex_id])

common_symbols = sorted(list(common_symbols))[:MAX_COINS]

print("\n==============================")
print(f"🔍 Выбрано {len(common_symbols)} монет для проверки.\n")

if len(common_symbols) == 0:
    print("❌ Нет общих монет. Бот остановлен.")
    exit()

# ------------------------------
# Объём
# ------------------------------
def get_orderbook_volume(ex, symbol):
    try:
        ob = ex.fetch_order_book(symbol)
        bid_volume = sum([p * a for p, a in ob['bids'][:5]])
        ask_volume = sum([p * a for p, a in ob['asks'][:5]])
        return max(bid_volume, ask_volume)
    except:
        return 0

# ------------------------------
# Основной цикл
# ------------------------------
print("📌 Старт бота...")

while True:
    print(f"\n{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    for symbol in common_symbols:
        volumes = []
        for ex_id, ex in working_exchanges.items():
            volumes.append(get_orderbook_volume(ex, symbol))

        if any(v < MIN_VOLUME for v in volumes):
            continue

        prices = {}
        for ex_id, ex in working_exchanges.items():
            try:
                ticker = ex.fetch_ticker(symbol)
                prices[ex_id] = ticker.get('last') or ticker.get('close') or ticker.get('bid')
            except:
                pass

        if len(prices) < 2:
            continue

        min_ex = min(prices, key=prices.get)
        max_ex = max(prices, key=prices.get)
        min_price = prices[min_ex]
        max_price = prices[max_ex]
        spread = (max_price - min_price) / min_price

        if spread >= SPREAD_THRESHOLD:
            msg = (
                f"🔥 Арбитраж найден!\n"
                f"{symbol}\n\n"
                f"Купить: {min_ex} — {min_price:.4f}\n"
                f"Продать: {max_ex} — {max_price:.4f}\n"
                f"СПРЕД: {spread * 100:.2f}%\n"
                f"Объём: {max(volumes):.2f} USD\n"
                f"Проверить актуальность: /check_{symbol}"
            )
            print(msg)
            send_message(msg)

    time.sleep(CHECK_INTERVAL)
