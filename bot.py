import ccxt
import requests
import time
from datetime import datetime, timezone
import json

# --------------------------
# Telegram
# --------------------------
TOKEN = "8546366016:AAEWSe8vsdlBhyboZzOgcPb8h9cDSj09A80"
CHAT_ID = "6590452577"

TG_URL = f"https://api.telegram.org/bot{TOKEN}"

def send_message(text, buttons=None):
    payload = {"chat_id": CHAT_ID, "text": text}

    if buttons:
        payload["reply_markup"] = json.dumps({
            "inline_keyboard": [[{"text": b[0], "callback_data": b[1]}] for b in buttons]
        })

    try:
        requests.post(f"{TG_URL}/sendMessage", data=payload)
    except:
        pass


# --------------------------
# Биржи
# --------------------------
exchanges = {
    'kucoin': ccxt.kucoin(),
    'bitrue': ccxt.bitrue(),
    'bitmart': ccxt.bitmart(),
    'gateio': ccxt.gateio(),
    'poloniex': ccxt.poloniex()
}

# --------------------------
# Настройки
# --------------------------
SPREAD_THRESHOLD = 0.015
MAX_COINS = 150
CHECK_INTERVAL = 60
MIN_VOLUME_USDT = 10000
MIN_ORDERBOOK_USD = 500



# --------------------------
# Загружаем USDT пары
# --------------------------
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


# --------------------------
# Общие пары
# --------------------------
common = set(exchange_pairs['kucoin'])
for ex in exchange_pairs:
    common = common.intersection(exchange_pairs[ex])

common = sorted(list(common))[:MAX_COINS]
print(f"🔍 Выбрано {len(common)} общих пар /USDT (лимит {MAX_COINS})")


# --------------------------
# Проверка стакана
# --------------------------
def depth_liquidity(orderbook):
    bids = orderbook["bids"][:3]
    asks = orderbook["asks"][:3]
    if not bids or not asks:
        return 0
    return sum([p * a for p, a in bids]) + sum([p * a for p, a in asks])


# --------------------------
# Проверка актуальности
# --------------------------
def check_spread(symbol):
    prices = {}

    for ex_name, ex in exchanges.items():
        try:
            ticker = ex.fetch_ticker(symbol)
            prices[ex_name] = ticker.get("last")
        except:
            pass

    if len(prices) < 2:
        return "Недостаточно данных"

    low_ex = min(prices, key=prices.get)
    high_ex = max(prices, key=prices.get)

    sp = (prices[high_ex] - prices[low_ex]) / prices[low_ex] * 100

    if sp < 0.5:
        return f"⛔ Спред сейчас {sp:.2f}%. Сделка не актуальна."
    else:
        return f"✅ Спред сейчас {sp:.2f}% ещё живой."


# --------------------------
# Основной цикл
# --------------------------
print("📌 Старт сканера...")

while True:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(now)

    for symbol in common:

        prices = {}
        volumes = {}
        depths = {}

        for ex_name, ex in exchanges.items():

            try:
                ticker = ex.fetch_ticker(symbol)
                last = ticker.get("last")
                vol = ticker.get("baseVolume") or 0

                if not last or last < 0.00001:  # защита от фантомных цен
                    continue

                volumes[ex_name] = last * vol

                if volumes[ex_name] < MIN_VOLUME_USDT:
                    continue

                ob = ex.fetch_order_book(symbol)
                d = depth_liquidity(ob)

                if d < MIN_ORDERBOOK_USD:
                    continue

                prices[ex_name] = last
            except:
                pass

        if len(prices) < 2:
            continue

        low_ex = min(prices, key=prices.get)
        high_ex = max(prices, key=prices.get)

        low_price = prices[low_ex]
        high_price = prices[high_ex]

        spread = (high_price - low_price) / low_price

        if spread > 10:  # фильтр мусорных спредов
            continue

        if spread >= SPREAD_THRESHOLD:

            button = [(f"Проверить актуальность", f"check_{symbol.replace('/','_')}")]

            msg = (
                f"🔥 Арбитраж! {symbol}\n\n"
                f"Купить: {low_ex} → {low_price:.8f}\n"
                f"Продать: {high_ex} → {high_price:.8f}\n\n"
                f"СПРЕД: {spread*100:.2f}%\n"
                f"Объём (USD): {max(volumes.values()):,.2f}\n"
                f"Время: {now}"
            )

            print(msg)
            send_message(msg, buttons=button)

    time.sleep(CHECK_INTERVAL)
