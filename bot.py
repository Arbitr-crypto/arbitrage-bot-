# bot.py — арбитражный Telegram-бот (без ИИ/API)
# Совместимая конфигурация: python-telegram-bot v20.3 (с job-queue)
# Установи зависимости: python-telegram-bot[job-queue]==20.3 ccxt pandas requests

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
    CallbackQueryHandler,
    ContextTypes,
)

# ------------------------------
# Настройка: токен / id
# ------------------------------
# Рекомендуемый способ — задать TELEGRAM_TOKEN в переменных окружения на Railway или хостинге.
# Для быстрой проверки локально можно временно вставить токен прямо сюда (НЕ публикуй).
TELEGRAM_TOKEN = 8546366016:AAEWSe8vsdlBhyboZzOgcPb8h9cDSj09A80 # <- поставь токен в env vars
# TELEGRAM_TOKEN = "1234567:ABC..."  # пример для локальной отладки (временно)

if not TELEGRAM_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN не задан. Положи TELEGRAM_TOKEN в env vars или вставь в код для теста.")

OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "0"))     # твой TG id (владелец)
OPERATOR_ID = int(os.environ.get("OPERATOR_ID", "0"))         # id оператора

# ------------------------------
# Биржи и параметры (можно менять через env)
# ------------------------------
EXCHANGE_IDS = os.environ.get("EXCHANGES", "kucoin,bitrue,bitmart,gateio,poloniex").split(",")
SPREAD_THRESHOLD = float(os.environ.get("SPREAD_THRESHOLD", 0.015))  # 1.5%
MIN_VOLUME_USD = float(os.environ.get("MIN_VOLUME_USD", 1500))       # 1500 USDT
MAX_COINS = int(os.environ.get("MAX_COINS", 150))                    # 150 пар
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 60))           # сек

DB_FILE = os.environ.get("ARBI_DB", "arbi_data.db")

# ------------------------------
# Инициализация ccxt клиентов (публичный доступ)
# ------------------------------
exchanges = {}
for ex_id in EXCHANGE_IDS:
    ex_id = ex_id.strip()
    if not ex_id:
        continue
    try:
        ex_cls = getattr(ccxt, ex_id)
        exchanges[ex_id] = ex_cls({'enableRateLimit': True})
        print(f"Инициализирован {ex_id}")
    except Exception as e:
        print(f"Ошибка инициализации {ex_id}: {e}")

# ------------------------------
# SQLite (whitelist, signals)
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
cur.execute("""
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT,
    buy_ex TEXT,
    sell_ex TEXT,
    initial_spread REAL,
    initial_time TEXT
)
""")
conn.commit()

# ------------------------------
# Утилиты whitelist
# ------------------------------
def is_whitelisted(tg_id: int) -> bool:
    cur.execute("SELECT 1 FROM whitelist WHERE tg_id=?", (tg_id,))
    return cur.fetchone() is not None

def add_whitelist(tg_id: int, added_by: int):
    cur.execute("INSERT OR REPLACE INTO whitelist (tg_id, added_by, added_at) VALUES (?, ?, ?)",
                (tg_id, added_by, datetime.now(timezone.utc).isoformat()))
    conn.commit()

def remove_whitelist(tg_id: int):
    cur.execute("DELETE FROM whitelist WHERE tg_id=?", (tg_id,))
    conn.commit()

def list_whitelist():
    cur.execute("SELECT tg_id, added_by, added_at FROM whitelist")
    return cur.fetchall()

# ------------------------------
# Валидация символов (USDT и фильтр "мусора")
# ------------------------------
def is_valid_symbol(symbol: str) -> bool:
    if not symbol.endswith("/USDT"):
        return False
    bad_keywords = ['3S','3L','UP','DOWN','BULL','BEAR','ETF','HALF','MOON','INVERSE']
    up = symbol.upper()
    for b in bad_keywords:
        if b in up:
            return False
    base = symbol.split("/")[0]
    if len(base) < 2 or len(base) > 20:
        return False
    return True

# ------------------------------
# Объём по top-3 уровней (синхронно и асинхронно)
# ------------------------------
def orderbook_volume_usd(exchange, symbol):
    try:
        ob = exchange.fetch_order_book(symbol, limit=5)
        bid_vol = sum([p*a for p,a in ob.get('bids', [])[:3]])
        ask_vol = sum([p*a for p,a in ob.get('asks', [])[:3]])
        return max(bid_vol, ask_vol)
    except Exception:
        return 0.0

async def orderbook_volume_usd_async(exchange, symbol):
    try:
        ob = await asyncio.to_thread(exchange.fetch_order_book, symbol, 5)
        bid_vol = sum([p*a for p,a in ob.get('bids', [])[:3]])
        ask_vol = sum([p*a for p,a in ob.get('asks', [])[:3]])
        return max(bid_vol, ask_vol)
    except Exception:
        return 0.0

# ------------------------------
# Отправка сигнала с inline-кнопкой
# ------------------------------
async def send_signal_to_whitelist(app, text, symbol, buy_ex, sell_ex, initial_spread):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Проверить спред", callback_data=f"check|{symbol}|{buy_ex}|{sell_ex}")],
        [InlineKeyboardButton("Поддержка", url="https://t.me/Arbitr_IP")]
    ])
    cur.execute("INSERT INTO signals (symbol, buy_ex, sell_ex, initial_spread, initial_time) VALUES (?, ?, ?, ?, ?)",
                (symbol, buy_ex, sell_ex, float(initial_spread), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    cur.execute("SELECT tg_id FROM whitelist")
    rows = cur.fetchall()
    if not rows:
        # если whitelist пуст — отправим владельцу (чтобы не терялись сигналы)
        if OWNER_CHAT_ID and OWNER_CHAT_ID != 0:
            try:
                await app.bot.send_message(chat_id=OWNER_CHAT_ID, text=f"Сигнал найден, но whitelist пуст:\n{text}")
            except Exception as e:
                print("Не могу отправить владельцу:", e)
        return
    for (tg_id,) in rows:
        try:
            await app.bot.send_message(chat_id=tg_id, text=text, reply_markup=keyboard)
        except Exception as e:
            print(f"Не удалось отправить сигнал {tg_id}: {e}")

# ------------------------------
# Callback: кнопка "Проверить спред"
# ------------------------------
async def check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    try:
        _, symbol, buy_ex, sell_ex = data.split("|")
    except Exception:
        await query.message.reply_text("Некорректные данные в callback.")
        return

    user_id = query.from_user.id
    if not is_whitelisted(user_id) and user_id not in (OWNER_CHAT_ID, OPERATOR_ID):
        await query.message.reply_text("🚫 У вас нет доступа.")
        return

    try:
        buy_client = exchanges[buy_ex]
        sell_client = exchanges[sell_ex]
    except KeyError:
        await query.message.reply_text("Ошибка: одна из бирж не инициализирована.")
        return

    try:
        ob_buy = await asyncio.to_thread(buy_client.fetch_order_book, symbol, 5)
        ob_sell = await asyncio.to_thread(sell_client.fetch_order_book, symbol, 5)
    except Exception as e:
        await query.message.reply_text(f"❗ Ошибка получения данных: {e}")
        return

    ask_price = ob_buy.get('asks')[0][0] if ob_buy.get('asks') else None
    bid_price = ob_sell.get('bids')[0][0] if ob_sell.get('bids') else None

    if not ask_price or not bid_price:
        await query.message.reply_text("❗ Не удалось получить лучшие цены.")
        return

    current_spread = (bid_price - ask_price) / ask_price
    cur.execute("SELECT initial_spread, initial_time FROM signals WHERE symbol=? AND buy_ex=? AND sell_ex=? ORDER BY id DESC LIMIT 1",
                (symbol, buy_ex, sell_ex))
    row = cur.fetchone()
    initial_spread = row[0] if row else None
    initial_time = row[1] if row else None

    if initial_spread is None:
        text = (f"🔄 Актуальный спред для {symbol}:\n"
                f"Купить: {buy_ex} → {ask_price:.6f}\n"
                f"Продать: {sell_ex} → {bid_price:.6f}\n"
                f"Текущий спред: {current_spread*100:.4f}%")
    else:
        diff = (current_spread - initial_spread)
        if abs(diff) < 1e-9:
            cmp_text = f"Спред такой же: {current_spread*100:.4f}%"
        elif diff < 0 and current_spread >= SPREAD_THRESHOLD:
            cmp_text = f"Спред уменьшился, но всё ещё актуален: {current_spread*100:.4f}% (изменение {diff*100:+.4f}%)"
        elif diff < 0 and current_spread < SPREAD_THRESHOLD:
            cmp_text = f"Спред уменьшился и стал ниже порога: {current_spread*100:.4f}% (изменение {diff*100:+.4f}%)"
        else:
            cmp_text = f"Спред увеличился: {current_spread*100:.4f}% (изменение {diff*100:+.4f}%)"

        text = (f"🔄 Обновление для {symbol}\n"
                f"Купить: {buy_ex} → {ask_price:.6f}\n"
                f"Продать: {sell_ex} → {bid_price:.6f}\n"
                f"{cmp_text}\n"
                f"Первый сигнал: {initial_spread*100:.4f}% (в {initial_time})")

    v_buy = await orderbook_volume_usd_async(exchanges[buy_ex], symbol)
    v_sell = await orderbook_volume_usd_async(exchanges[sell_ex], symbol)
    text += f"\nОбъём (approx USD): buy={v_buy:.2f}, sell={v_sell:.2f}"
    text += f"\nРекомендуемая сеть: TBD"

    await query.message.reply_text(text)

# ------------------------------
# Команды: /start, /add_user, /remove_user, /list_users
# ------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # реферальная ссылка: https://t.me/<bot_username>?start=<user_id>
    user_id = update.effective_user.id
    bot = context.bot
    me = await bot.get_me()
    bot_username = me.username or "this_bot"
    ref_link = f"https://t.me/{bot_username}?start={user_id}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Моя рефссылка", url=ref_link)],
        [InlineKeyboardButton("Проверить один спред", callback_data="manual_check")],
        [InlineKeyboardButton("Поддержка", url="https://t.me/Arbitr_IP")]
    ])
    await update.message.reply_text("Добро пожаловать! Используй кнопки ниже.", reply_markup=keyboard)

async def cmd_add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.id
    if caller not in (OWNER_CHAT_ID, OPERATOR_ID):
        await update.message.reply_text("🚫 Только владелец или оператор могут управлять whitelist.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /add_user <tg_id>")
        return
    try:
        tg_id = int(context.args[0])
        add_whitelist(tg_id, caller)
        await update.message.reply_text(f"✅ Пользователь {tg_id} добавлен в whitelist.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.id
    if caller not in (OWNER_CHAT_ID, OPERATOR_ID):
        await update.message.reply_text("🚫 Только владелец или оператор могут управлять whitelist.")
        return
    if not context.args:
        await update.message.reply_text("Использование: /remove_user <tg_id>")
        return
    try:
        tg_id = int(context.args[0])
        remove_whitelist(tg_id)
        await update.message.reply_text(f"✅ Пользователь {tg_id} удалён из whitelist.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка: {e}")

async def cmd_list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.id
    if caller not in (OWNER_CHAT_ID, OPERATOR_ID):
        await update.message.reply_text("🚫 Только владелец или оператор могут просматривать whitelist.")
        return
    rows = list_whitelist()
    if not rows:
        await update.message.reply_text("Whitelist пуст.")
        return
    txt = "Whitelist:\n" + "\n".join([f"{r[0]} (added_by={r[1]}) at {r[2]}" for r in rows])
    await update.message.reply_text(txt)

# ------------------------------
# Основной сканер (одна итерация) — асинхронно вызывает ccxt через asyncio.to_thread
# ------------------------------
async def scanner_iteration(app):
    exchange_pairs = {}
    for ex_name, ex in exchanges.items():
        try:
            markets = await asyncio.to_thread(ex.load_markets)
            usdt_pairs = [s for s in markets.keys() if is_valid_symbol(s)]
            exchange_pairs[ex_name] = set(usdt_pairs)
            print(f"✔ {ex_name} — {len(usdt_pairs)} символов /USDT")
        except Exception as e:
            exchange_pairs[ex_name] = set()
            print(f"❌ Ошибка {ex_name}: {e}")

    symbol_map = {}
    for ex_name, pairs in exchange_pairs.items():
        for s in pairs:
            symbol_map.setdefault(s, []).append(ex_name)
    common_symbols = [s for s, exs in symbol_map.items() if len(exs) >= 2]
    common_symbols = sorted(common_symbols)[:MAX_COINS]
    print(f"🔍 Выбрано {len(common_symbols)} общих пар /USDT (лимит {MAX_COINS})")

    for symbol in common_symbols:
        ex_list = symbol_map[symbol]
        for buy_ex in ex_list:
            for sell_ex in ex_list:
                if buy_ex == sell_ex:
                    continue
                try:
                    ask_book = await asyncio.to_thread(exchanges[buy_ex].fetch_order_book, symbol, 5)
                    bid_book = await asyncio.to_thread(exchanges[sell_ex].fetch_order_book, symbol, 5)
                except Exception:
                    continue
                if not ask_book.get('asks') or not bid_book.get('bids'):
                    continue
                ask_price, ask_amt = ask_book['asks'][0]
                bid_price, bid_amt = bid_book['bids'][0]
                if ask_price <= 0:
                    continue
                spread = (bid_price - ask_price) / ask_price
                approx_vol = max(await orderbook_volume_usd_async(exchanges[buy_ex], symbol),
                                 await orderbook_volume_usd_async(exchanges[sell_ex], symbol))
                if approx_vol < MIN_VOLUME_USD:
                    continue
                if spread < SPREAD_THRESHOLD:
                    continue
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                text = (f"🔥Арбитраж! {symbol}\n"
                        f"Купить: {buy_ex} → {ask_price:.6f}\n"
                        f"Продать: {sell_ex} → {bid_price:.6f}\n"
                        f"СПРЕД: {spread*100:.4f}%\n"
                        f"Объём (USD): {approx_vol:.2f}\n"
                        f"Время: {now}")
                print(text)
                await send_signal_to_whitelist(app, text, symbol, buy_ex, sell_ex, spread)

# ------------------------------
# Job callback (ptb ожидает job_callback(context))
# ------------------------------
async def job_callback(context: ContextTypes.DEFAULT_TYPE):
    # context.application передаёт Application
    await scanner_iteration(context.application)

# ------------------------------
# Собираем приложение и запускаем polling
# ------------------------------
def build_application():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    # хендлеры
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("add_user", cmd_add_user))
    app.add_handler(CommandHandler("remove_user", cmd_remove_user))
    app.add_handler(CommandHandler("list_users", cmd_list_users))
    app.add_handler(CallbackQueryHandler(check_callback, pattern=r"^check\|"))
    return app

def main():
    app = build_application()

    # регистрируем периодическую задачу через job_queue
    # (job_queue создаётся внутри Application при установке PTB с job-queue extras)
    if app.job_queue is None:
        print("⚠️ job_queue отсутствует — убедись, что установил python-telegram-bot[job-queue].")
    else:
        app.job_queue.run_repeating(job_callback, interval=CHECK_INTERVAL, first=5)

    print("Запуск бота...")
    app.run_polling()

if __name__ == "__main__":
    main()
