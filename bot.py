import ccxt
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
        requests.post(url, data=payload)
    except:
        pass

# ------------------------------
# Биржи (публичные)
# ------------------------------
exchanges = {
    'kucoin': ccxt.kucoin(),
    'bitrue': ccxt.bitrue(),
    'bitmart': ccxt.bitmart(),
    'gateio': ccxt.gateio(),
    'poloniex': ccxt.poloniex()
}

# ------------------------------
# Настройки
# ------------------------------
SPREAD_THRESHOLD = 0.015
MAX_COINS = 150
CHECK_INTERVAL = 60
MIN_VOLUME = 200

# ------------------------------
# Загружаем USDT пары
# ------------------------------
print("📌 Загружаю пары (USDT)...")
exchange_pairs = {}

for ex_name, ex in exchanges.items():
    try:
        markets = ex.load_markets()
        usdt_pairs = [s for s in markets if s.endswith("/USDT")]
        exchange_pairs[ex_name] = usdt_pairs
        print(f"✔ {ex_name.upper()} — {len(usdt_pairs)} символов /USDT")
    except Exception as e:
        exchange_pairs[ex_name] = []
        print(f"❌ Ошибка {ex_name}: {e}")

# ------------------------------
# Общие пары
# ------------------------------
common = set(exchange_pairs['kucoin'])
for ex in exchange_pairs:
    common = common.intersection(exchange_pairs[ex])

common = sorted(list(common))[:MAX_COINS]
print(f"🔍 Выбрано {len(common)} общих пар /USDT (лимит {MAX_COINS})")

# ------------------------------
# Функция объёмов
# ------------------------------
def volume(ex, symbol):
    try:
        ob = ex.fetch_order_book(symbol)
        return sum([p*a for p,a in ob['bids'][:3]]) + sum([p*a for p,a in ob['asks'][:3]])
    except:
        return 0

# ------------------------------
# Основной сканер
# ------------------------------
print("📌 Старт сканера...")

while True:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(now)

    for symbol in common:

        # Сбор цен
        prices = {}
        vols = {}

        for ex_name, ex in exchanges.items():
            try:
                ticker = ex.fetch_ticker(symbol)
                price = ticker.get("last") or ticker.get("close")
                if price:
                    prices[ex_name] = price
                    vols[ex_name] = volume(ex, symbol)
            except:
                pass

        if len(prices) < 2:
            continue

        # Проверка объемов
        if any(v < MIN_VOLUME for v in vols.values()):
            continue

        low_ex = min(prices, key=prices.get)
        high_ex = max(prices, key=prices.get)
        low_price = prices[low_ex]
        high_price = prices[high_ex]

        spread = (high_price - low_price) / low_price

        if spread >= SPREAD_THRESHOLD:
            msg = (
                f"🔥 Арбитраж! {symbol}\n"
                f"Купить: {low_ex} → {low_price:.8f}\n"
                f"Продать: {high_ex} → {high_price:.8f}\n"
                f"СПРЕД: {spread*100:.4f}%\n"
                f"Объём (USD): {max(vols.values()):.2f}\n"
                f"Проверить актуальность: /check_{symbol.replace('/','_')}\n"
                f"Время: {now}"
            )
            print(msg)
            send_message(msg)

    time.sleep(CHECK_INTERVAL)
