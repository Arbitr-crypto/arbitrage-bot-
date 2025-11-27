# bot.py — рабочая версия для Railway / локального запуска
import os
import ccxt
import time
import sqlite3
import asyncio
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)

# ------------------------------
# ENV ПЕРЕМЕННЫЕ (Railway)
# ------------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN не задан в переменных окружения!")

OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "0"))
OPERATOR_ID = int(os.environ.get("OPERATOR_ID", "0"))

SPREAD_THRESHOLD = float(os.environ.get("SPREAD_THRESHOLD", 0.015))
MIN_VOLUME_USD = float(os.environ.get("MIN_VOLUME_USD", 1500))
MAX_COINS = int(os.environ.get("MAX_COINS", 150))
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 60))

DB_FILE = os.environ.get("ARBI_DB", "arbi_data.db")

# Биржи
EXCHANGE_IDS = ['kucoin', 'bitrue', 'bitmart', 'gateio', 'poloniex']

# ------------------------------
# ИНИЦИАЛИЗАЦИЯ BIRZH CCXT
# ------------------------------
exchanges = {}
for ex_id in EXCHANGE_IDS:
    try:
        exchanges[ex_id] = getattr(ccxt, ex_id)({'enableRateLimit': True})
        print(f"Инициализирован {ex_id}")
    except Exception as e:
        print(f"Ошибка инициализации {ex_id}: {e}")

# ------------------------------
# БАЗА ДАННЫХ
# ------------------------------
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS whitelist (
    tg_id INTEGER PRIMARY KEY,
    added_by INTEGER,
    added_at TEXT
)
""")
conn.commit()

# ------------------------------
# WHITELIST
# ------------------------------
def is_whitelisted(id):
    cur.execute("SELECT 1 FROM whitelist WHERE tg_id=?", (id,))
    return cur.fetchone() is not None

def add_whitelist(id, by):
    cur.execute(
        "INSERT OR REPLACE INTO whitelist VALUES (?, ?, ?)",
        (id, by, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()

# ------------------------------
# ФИЛЬТР СИМВОЛОВ
# ------------------------------
def is_valid_symbol(symbol):
    if not symbol.endswith("/USDT"):
        return False
    bad = ['3S','3L','UP','DOWN','BULL','BEAR','ETF','HALF','MOON','INVERSE']
    for b in bad:
        if b in symbol.upper():
            return False
    base = symbol.split("/")[0]
    return 2 <= len(base) <= 20

# ------------------------------
# ОБЪЁМ
# ------------------------------
def orderbook_volume_usd(ex, symbol):
    try:
        ob = ex.fetch_order_book(symbol, limit=5)
        bid = sum([p*a for p,a in ob.get("bids", [])[:3]])
        ask = sum([p*a for p,a in ob.get("asks", [])[:3]])
        return max(bid, ask)
    except:
        return 0

# ------------------------------
# ОТПРАВКА СИГНАЛА
# ------------------------------
async def send_signal(app, txt):
    cur.execute("SELECT tg_id FROM whitelist")
    for (uid,) in cur.fetchall():
        try:
            await app.bot.send_message(uid, txt)
        except Exception as e:
            print(f"Не отправлено {uid}: {e}")

# ------------------------------
# СКАНЕР
# ------------------------------
async def scanner(app):
    while True:
        try:
            print("🔍 Сканирую...")

            # Загружаем пары
            ex_pairs = {}
            for name, ex in exchanges.items():
                try:
                    mk = ex.load_markets()
                    usdt = [s for s in mk if is_valid_symbol(s)]
                    ex_pairs[name] = set(usdt)
                    print(f"{name}: {len(usdt)} пар")
                except Exception as e:
                    ex_pairs[name] = set()
                    print(f"Ошибка {name}: {e}")

            # Находим общие 150 монет
            sym_map = {}
            for ex, pairs in ex_pairs.items():
                for s in pairs:
                    sym_map.setdefault(s, []).append(ex)

            common = sorted([s for s, lst in sym_map.items() if len(lst) >= 2])[:MAX_COINS]

            for s in common:
                lst = sym_map[s]
                for b in lst:
                    for sl in lst:
                        if b == sl:
                            continue
                        try:
                            ob1 = exchanges[b].fetch_order_book(s)
                            ob2 = exchanges[sl].fetch_order_book(s)
                            ask = ob1['asks'][0][0]
                            bid = ob2['bids'][0][0]
                        except:
                            continue

                        if ask <= 0:
                            continue

                        spread = (bid - ask) / ask
                        if spread < SPREAD_THRESHOLD:
                            continue

                        vol = max(orderbook_volume_usd(exchanges[b], s),
                                  orderbook_volume_usd(exchanges[sl], s))
                        if vol < MIN_VOLUME_USD:
                            continue

                        now = datetime.utcnow().strftime("%H:%M:%S")
                        msg = (f"🔥 Арбитраж: {s}\n"
                               f"Купить: {b} → {ask}\n"
                               f"Продать: {sl} → {bid}\n"
                               f"СПРЕД: {spread*100:.3f}%\n"
                               f"Объём: {vol:.2f}\n"
                               f"Время: {now}")

                        await send_signal(app, msg)

        except Exception as e:
            print("Ошибка сканера:", e)

        await asyncio.sleep(CHECK_INTERVAL)

# ------------------------------
# /add_user
# ------------------------------
async def cmd_add(update: Update, ctx):
    if update.effective_user.id != OWNER_CHAT_ID:
        return await update.message.reply_text("❌ Нет доступа")
    try:
        uid = int(ctx.args[0])
        add_whitelist(uid, OWNER_CHAT_ID)
        await update.message.reply_text("Добавлен!")
    except:
        await update.message.reply_text("Ошибка формата")

# ------------------------------
# INIT + RUN
# ------------------------------
async def post_init(app):
    app.create_task(scanner(app))

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("add_user", cmd_add))
    print("🚀 Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()


