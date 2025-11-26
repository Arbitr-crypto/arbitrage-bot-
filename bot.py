import ccxt
import pandas as pd
import requests
import time
from datetime import datetime, UTC

# --------------------------------
# Telegram
# --------------------------------
TOKEN = "8546366016:AAEWSe8vsdlBhyboZzOgcPb8h9cDSj09A80"
CHAT_ID = "6590452577"
OWNER = "@Fgfgfgggffgg"

def send_message(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, data=payload)
        return r.json()
    except Exception as e:
        print("Ошибка Telegram:", e)

# --------------------------------
# Биржи
# --------------------------------

# 🔥 HTX — через API ключи (только для чтения)
HTX_API_KEY = "6b42ef88-vfd5ghr532-b4405eb0-8028a"
HTX_SECRET  = "f1c7cce3-8ca53bf4-e54117a9c32b5"

exchanges = {
    'kucoin': ccxt.kucoin(),
    'bitrue': ccxt.bitrue(),
    'bitmart': ccxt.bitmart(),
    'gateio': ccxt.gateio(),
    'poloniex': ccxt.poloniex(),
    'htx': ccxt.huobi({
        "apiKey": HTX_API_KEY,
        "secret": HTX_SECRET
    })
}

# --------------------------------
# Настройки
# --------------------------------
SPREAD_THRESHOLD = 0.02  # 2%
MIN_VOLUME = 300
CHECK_INTERVAL = 60
MAX_COINS = 100  # максимум монет

# ❗ Токены которые мы НЕ анализируем
BAN_PATTERNS = [
    "3L", "3S", "5L", "5S",
    "UP", "DOWN",
    "BULL", "BEAR",
    "ETF", 
    "PERP", "-", "FUTURE"
]

def is_allowed_symbol(symbol):
    if not symbol.endswith("/USDT"):
        return False
    for bad in BAN_PATTERNS:
        if bad in symbol.upper():
            return False
    return True

# --------------------------------
# Загружаем пары
# --------------------------------
print("📌 Загружаю пары (чистый USDT)...")

exchange_symbols = {}

for ex_id, ex in exchanges.items():
    try:
        markets = ex.load_markets()
        symbols = [s for s in markets if is_allowed_symbol(s)]
        exchange_symbols[ex_id] = symbols
        print(f"✔ {ex_id.upper()} — {len(symbols)} символов /USDT")
    except Exception as e:
        exchange_symbols[ex_id] = []
        print(f"❌ Ошибка {ex_id}: {e}")

# Общие символы
common = set(exchange_symbols['kucoin'])
for ex_id in exchanges:
    common = common.intersection(exchange_symbols[ex_id])

common = sorted(list(common))[:MAX_COINS]

print(f"🔍 Выбрано {len(common)} общих пар /USDT")

# --------------------------------
# Объём из стакана
# --------------------------------
def get_volume(ex, symbol):
    try:
        ob = ex.fetch_order_book(symbol)
        bid_vol = sum([p * a for p, a in ob['bids'][:5]])
        ask_vol = sum([p * a for p, a in ob['asks'][:5]])
        return max(bid_vol, ask_vol)
    except:
        return 0

# --------------------------------
# Основной цикл
# --------------------------------
print("📌 Старт сканера...\n")

while True:
    print(datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"))

    for symbol in common:
        volumes = []
        for ex_id, ex in exchanges.items():
            volumes.append(get_volume(ex, symbol))

        if any(v < MIN_VOLUME for v in volumes):
            continue

        prices = {}
        for ex_id, ex in exchanges.items():
            try:
                t = ex.fetch_ticker(symbol)
                px = t.get("last") or t.get("close")
                if px:
                    prices[ex_id] = px
            except:
                pass

        if len(prices) < 2:
            continue

        buy_ex = min(prices, key=prices.get)
        sell_ex = max(prices, key=prices.get)
        p_buy = prices[buy_ex]
        p_sell = prices[sell_ex]

        spread = (p_sell - p_buy) / p_buy

        if spread >= SPREAD_THRESHOLD:
            msg = (
                f"🔥 <b>Арбитраж найден!</b>\n"
                f"<b>{symbol}</b>\n"
                f"Купить: {buy_ex} → <b>{p_buy}</b>\n"
                f"Продать: {sell_ex} → <b>{p_sell}</b>\n"
                f"СПРЕД: <b>{spread*100:.2f}%</b>\n"
                f"Объём: <b>{max(volumes):.2f} USD</b>\n"
                f"Проверить: /check_{symbol.replace('/', '_')}\n"
                f"Время: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
            print(msg)
            send_message(msg)

    time.sleep(CHECK_INTERVAL)

