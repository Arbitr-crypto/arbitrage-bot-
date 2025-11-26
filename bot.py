# bot.py — обновлённый
import os
import ccxt
import requests
import time
from datetime import datetime, timezone
from decimal import Decimal, getcontext

# Установим точность Decimal (достаточно 12 знаков)
getcontext().prec = 12

# ------------------------------
# Конфиг через переменные окружения
# ------------------------------
TELEGRAM_TOKEN = os.environ.get("8546366016:AAEWSe8vsdlBhyboZzOgcPb8h9cDSj09A80", "")
TELEGRAM_CHAT_ID = os.environ.get("6590452577", "")  # можно оставить пустым если используешь send_message для всех whitelist
OWNER_USERNAME = os.environ.get("Fgfgfgggffgg", "owner_username")

# HTX API ключи (чтение)
HTX_API_KEY = os.environ.get("HTX_API_KEY")
HTX_API_SECRET = os.environ.get("HTX_API_SECRET")

# Список пользователей, которым бот отправляет сигналы (whitelist).
# В продакшене хранить в БД; тут — через env (для теста можно разделить запятую)
WHITELIST = [int(x) for x in os.environ.get("WHITELIST", "").split(",") if x.strip()]

# ------------------------------
# Параметры сканера
# ------------------------------
SPREAD_THRESHOLD = Decimal(os.environ.get("SPREAD_THRESHOLD", "0.02"))  # 0.02 = 2%
MIN_VOLUME_USD = Decimal(os.environ.get("MIN_VOLUME_USD", "200"))       # $200
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))           # s
MAX_COINS = int(os.environ.get("MAX_COINS", "150"))                    # лимит пар для теста
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.15"))         # задержка между запросами к биржам (s)

# ------------------------------
# Telegram helper
# ------------------------------
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": str(chat_id), "text": text}
    try:
        r = requests.post(url, data=payload, timeout=10)
        return r.json()
    except Exception as e:
        print("Ошибка Telegram:", e)
        return None

# ------------------------------
# Инициализация бирж (HTX с ключами, остальные — публично)
# ------------------------------
exchanges = {}

# KuCoin, Bitrue, Bitmart, Gateio, Poloniex — публичный доступ
for exid in ['kucoin', 'bitrue', 'bitmart', 'gateio', 'poloniex']:
    try:
        exchanges[exid] = getattr(ccxt, exid)({'timeout': 10000, 'enableRateLimit': True})
    except Exception as e:
        print(f"Ошибка инициализации {exid}: {e}")

# HTX — используем API-ключи (только чтение/публичные эндпоинты)
try:
    if HTX_API_KEY and HTX_API_SECRET:
        exchanges['htx'] = ccxt.htx({
            'apiKey': HTX_API_KEY,
            'secret': HTX_API_SECRET,
            'timeout': 10000,
            'enableRateLimit': True
        })
    else:
        # fallback — публичный клиент (если нет ключей)
        exchanges['htx'] = ccxt.htx({'timeout': 10000, 'enableRateLimit': True})
except Exception as e:
    print("Ошибка инициализации HTX:", e)

# Удаляем биржи с неудачной инициализацией
bad = [k for k,v in exchanges.items() if v is None]
for b in bad:
    exchanges.pop(b, None)

print("Инициализация бирж завершена:", list(exchanges.keys()))

# ------------------------------
# Функции утилиты
# ------------------------------
def is_valid_usdt_symbol(symbol: str) -> bool:
    # ccxt uses "BTC/USDT" format; убираем все, которые не оканчиваются на /USDT
    return isinstance(symbol, str) and symbol.upper().endswith("/USDT")

def safe_fetch_order_book(exchange, symbol):
    try:
        ob = exchange.fetch_order_book(symbol, limit=10)  # берем топ 10
        return ob
    except Exception as e:
        # иногда API возвращает ошибку — просто логируем
        # print(f"fetch_order_book error {exchange.id} {symbol}: {e}")
        return None

def safe_fetch_ticker(exchange, symbol):
    try:
        t = exchange.fetch_ticker(symbol)
        return t
    except Exception:
        return None

def decimal_from(x):
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0")

def pretty_price(p: Decimal) -> str:
    # форматируем цену с динамическим количеством знаков
    if p >= Decimal("1"):
        return f"{p:.6f}"
    else:
        # для мелких цен — больше знаков
        return f"{p:.8f}"

# ------------------------------
# Подготовка списка общих символов (только /USDT)
# ------------------------------
print("Загружаю рынки с бирж...")
exchange_symbols = {}
for ex_id, ex in exchanges.items():
    try:
        ex.load_markets()
        symbols = [s for s in ex.symbols if is_valid_usdt_symbol(s)]
        exchange_symbols[ex_id] = set(symbols)
        print(f"✔ {ex_id.upper()} загружено {len(symbols)} символов /USDT")
        time.sleep(REQUEST_DELAY)
    except Exception as e:
        exchange_symbols[ex_id] = set()
        print(f"❌ Ошибка {ex_id}: {e}")

# находим символы, которые есть минимум на двух биржах
symbol_exchanges = {}
for ex_id, symbols in exchange_symbols.items():
    for s in symbols:
        symbol_exchanges.setdefault(s, []).append(ex_id)

common_symbols = [s for s, exs in symbol_exchanges.items() if len(exs) >= 2]
common_symbols = sorted(common_symbols)[:MAX_COINS]
print(f"🔍 Выбрано {len(common_symbols)} общих пар /USDT (лимит {MAX_COINS})")

# ------------------------------
# Основной сканер
# ------------------------------
print("📌 Старт сканера...")
while True:
    now = datetime.now(timezone.utc)
    print("\n", now.strftime("%Y-%m-%d %H:%M:%S UTC"))
    for symbol in common_symbols:
        # По каждой бирже получаем orderbook (в цикле, с задержкой)
        orderbooks = {}
        volumes_usd = {}
        prices_latest = {}
        for ex_id in symbol_exchanges[symbol]:
            ex = exchanges.get(ex_id)
            if not ex:
                continue
            ob = safe_fetch_order_book(ex, symbol)
            time.sleep(REQUEST_DELAY)
            if not ob:
                continue
            # берём лучшие ask (sell) и best bid (buy)
            best_ask = ob['asks'][0] if ob['asks'] else None
            best_bid = ob['bids'][0] if ob['bids'] else None
            if not best_ask or not best_bid:
                continue
            ask_price = decimal_from(best_ask[0])
            ask_amount = decimal_from(best_ask[1])
            bid_price = decimal_from(best_bid[0])
            bid_amount = decimal_from(best_bid[1])
            # вычислим "долларовый" объём топ-ордеров (примерно)
            vol_ask_usd = ask_price * ask_amount
            vol_bid_usd = bid_price * bid_amount
            volumes_usd[ex_id] = max(vol_ask_usd, vol_bid_usd)
            # для сравнения цен возьмем среднюю из best bid и ask (последняя цена)
            prices_latest[ex_id] = (ask_price + bid_price) / Decimal("2")
            orderbooks[ex_id] = {'ask_price': ask_price, 'ask_amount': ask_amount,
                                 'bid_price': bid_price, 'bid_amount': bid_amount}

        if len(orderbooks) < 2:
            continue

        # теперь перебираем пары (buy на бирже A, sell на бирже B)
        ex_list = list(orderbooks.keys())
        for i in range(len(ex_list)):
            for j in range(len(ex_list)):
                if i == j:
                    continue
                ex_buy = ex_list[i]   # где покупаем (берём ask)
                ex_sell = ex_list[j]  # где продаём (берём bid)
                ask_price = orderbooks[ex_buy]['ask_price']
                bid_price = orderbooks[ex_sell]['bid_price']
                ask_vol = orderbooks[ex_buy]['ask_amount']
                bid_vol = orderbooks[ex_sell]['bid_amount']
                # игнорируем странные нулевые цены/объёмы
                if ask_price <= Decimal("0") or bid_price <= Decimal("0"):
                    continue
                # проверка объёма USD на обеих сторонах
                if volumes_usd.get(ex_buy, Decimal("0")) < MIN_VOLUME_USD or volumes_usd.get(ex_sell, Decimal("0")) < MIN_VOLUME_USD:
                    continue
                # рассчитываем спред в процентах
                spread_pct = (bid_price - ask_price) / ask_price
                if spread_pct >= SPREAD_THRESHOLD:
                    # сформируем сообщение с высокой точностью
                    msg = (
                        f"🔥 Арбитраж! {symbol}\n"
                        f"Купить: {ex_buy} → {pretty_price(ask_price)}\n"
                        f"Продать: {ex_sell} → {pretty_price(bid_price)}\n"
                        f"СПРЕД: { (spread_pct * Decimal('100')):.4f}%\n"
                        f"Объём (USD, прибл.): {max(volumes_usd.get(ex_buy,0), volumes_usd.get(ex_sell,0)):.2f}\n"
                        f"Проверить актуальность: /check_{symbol.replace('/','_')}\n"
                        f"Время: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    )
                    print(msg)
                    # отправляем всем в WHITELIST (если пуст — отправляем на TELEGRAM_CHAT_ID)
                    targets = WHITELIST if WHITELIST else ([int(TELEGRAM_CHAT_ID)] if TELEGRAM_CHAT_ID else [])
                    for user in targets:
                        try:
                            send_message(user, msg)
                            time.sleep(0.05)
                        except Exception as e:
                            print("Ошибка отправки:", e)

    # пауза перед следующей итерацией
    time.sleep(CHECK_INTERVAL)

