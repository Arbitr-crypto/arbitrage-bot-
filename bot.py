  # bot.py — обновлённый арбитражный бот с whitelist, оператором и кнопкой "Проверить спред"
import os
import ccxt
import json
import time
import sqlite3
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler

# ------------------------------
# Конфигурация (через env vars на Railway)
# ------------------------------
TELEGRAM_TOKEN = os.environ.get("8546366016:AAEWSe8vsdlBhyboZzOgcPb8h9cDSj09A80")  # обязателен
OWNER_CHAT_ID = int(os.environ.get("6590452577", "0"))  # твой Telegram ID (владелец)
OPERATOR_ID = int(os.environ.get("8193755967", "0"))      # ID оператора (может управлять whitelist)

# Биржи — публичный доступ (если потом добавишь ключи, расскажу как)
EXCHANGE_IDS = ['kucoin', 'bitrue', 'bitmart', 'gateio', 'poloniex']

# Параметры фильтрации (можешь менять)
SPREAD_THRESHOLD = float(os.environ.get("SPREAD_THRESHOLD", 0.015))  # 1.5%
MIN_VOLUME_USD = float(os.environ.get("MIN_VOLUME_USD", 1500))       # 1500 USDT
MAX_COINS = int(os.environ.get("MAX_COINS", 150))                    # 150 пар
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", 60))           # сек

# Файл/БД для whitelist (используем SQLite для сохранности)
DB_FILE = os.environ.get("ARBI_DB", "arbi_data.db")

# ------------------------------
# Инициализация CCXT (публичные клиенты)
# ------------------------------
exchanges = {}
for ex_id in EXCHANGE_IDS:
    try:
        ex_cls = getattr(ccxt, ex_id)
        exchanges[ex_id] = ex_cls({'enableRateLimit': True})
    except Exception as e:
        print(f"Ошибка инициализации {ex_id}: {e}")

# ------------------------------
# Инициализация БД (SQLite) для whitelist и сохранения последних сигналов
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
# Утилиты: whitelist управление
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
# Фильтры "мусора" — удаляем левередж-токены, пары не /USDT и т.п.
# ------------------------------
def is_valid_symbol(symbol: str) -> bool:
    # Только USDT (строго)
    if not symbol.endswith("/USDT"):
        return False
    # исключаем маркеры левереджа/ETF
    bad_keywords = ['3S','3L','UP','DOWN','BULL','BEAR','ETF','HALF','MOON','INVERSE']
    up = symbol.upper()
    for b in bad_keywords:
        if b in up:
            return False
    # простая длина и формат - исключаем weird names
    if len(symbol.split("/")[0]) < 2 or len(symbol.split("/")[0]) > 20:
        return False
    return True

# ------------------------------
# Вспомогательные: вычисление объёма в USD (по топ-3 ордерам)
# ------------------------------
def orderbook_volume_usd(exchange, symbol):
    try:
        ob = exchange.fetch_order_book(symbol, limit=5)
        bid_vol = sum([p*a for p,a in ob.get('bids', [])[:3]])
        ask_vol = sum([p*a for p,a in ob.get('asks', [])[:3]])
        return max(bid_vol, ask_vol)
    except Exception:
        return 0.0

# ------------------------------
# Telegram: сообщение о найденном спреде + inline кнопка "Проверить спред"
# ------------------------------
async def send_signal_to_whitelist(app, text, symbol, buy_ex, sell_ex, initial_spread):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Проверить спред", callback_data=f"check|{symbol}|{buy_ex}|{sell_ex}")]
    ])
    # сохраняем сигнал в БД, чтобы можно было сравнивать при проверке
    cur.execute("INSERT INTO signals (symbol, buy_ex, sell_ex, initial_spread, initial_time) VALUES (?, ?, ?, ?, ?)",
                (symbol, buy_ex, sell_ex, float(initial_spread), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    # отправляем всем из whitelist
    cur.execute("SELECT tg_id FROM whitelist")
    rows = cur.fetchall()
    for (tg_id,) in rows:
        try:
            await app.bot.send_message(chat_id=tg_id, text=text, reply_markup=keyboard)
        except Exception as e:
            print(f"Не удалось отправить сигнал {tg_id}: {e}")

# ------------------------------
# Callback для inline-кнопки "Проверить спред"
# ------------------------------
async def check_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data  # формат check|SYMBOL|BUY_EX|SELL_EX
    _, symbol, buy_ex, sell_ex = data.split("|")
    user_id = query.from_user.id

    # проверка прав
    if not is_whitelisted(user_id) and user_id not in (OWNER_CHAT_ID, OPERATOR_ID):
        await query.message.reply_text(f"🚫 У вас нет доступа. Свяжитесь с @{os.environ.get('OWNER_USERNAME', 'owner')}")
        return

    # получаем текущие цены
    try:
        buy_client = exchanges[buy_ex]
        sell_client = exchanges[sell_ex]
        ob_buy = buy_client.fetch_order_book(symbol, limit=5)
        ob_sell = sell_client.fetch_order_book(symbol, limit=5)
        ask_price = ob_buy['asks'][0][0] if ob_buy['asks'] else None
        bid_price = ob_sell['bids'][0][0] if ob_sell['bids'] else None
    except Exception as e:
        await query.message.reply_text(f"❗ Ошибка получения данных: {e}")
        return

    if not ask_price or not bid_price:
        await query.message.reply_text("❗ Не удалось получить лучшую цену на одной из бирж.")
        return

    current_spread = (bid_price - ask_price) / ask_price
    # извлечём последний сигнал для этой пары из БД (самый последний по symbol+buy/sell)
    cur.execute("SELECT initial_spread, initial_time FROM signals WHERE symbol=? AND buy_ex=? AND sell_ex=? ORDER BY id DESC LIMIT 1",
                (symbol, buy_ex, sell_ex))
    row = cur.fetchone()
    initial_spread = row[0] if row else None
    initial_time = row[1] if row else None

    # Сравнение и формирование ответа
    if initial_spread is None:
        # нет информации — просто вывести текущий спред
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

    # Добавим информацию по объёму и предложенную сеть (placeholder)
    v_buy = orderbook_volume_usd(exchanges[buy_ex], symbol)
    v_sell = orderbook_volume_usd(exchanges[sell_ex], symbol)
    text += f"\nОбъём (approx USD): buy={v_buy:.2f}, sell={v_sell:.2f}"
    text += f"\nРекомендуемая сеть: TBD (будет подключено API бирж для вывода)"

    await query.message.reply_text(text)

# ------------------------------
# Команды управления whitelist (доступно владельцу и оператору)
# ------------------------------
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
# Основной сканер (одноразовая проверка для каждой итерации)
# ------------------------------
async def scanner_loop(app):
    # перебираем markets и формируем список общих валидных пар
    exchange_pairs = {}
    for ex_name, ex in exchanges.items():
        try:
            markets = ex.load_markets()
            usdt_pairs = [s for s in markets.keys() if is_valid_symbol(s)]
            exchange_pairs[ex_name] = set(usdt_pairs)
            print(f"✔ {ex_name} — {len(usdt_pairs)} символов /USDT")
        except Exception as e:
            exchange_pairs[ex_name] = set()
            print(f"❌ Ошибка {ex_name}: {e}")

    # общие пары, которые есть минимум на двух биржах
    symbol_map = {}
    for ex_name, pairs in exchange_pairs.items():
        for s in pairs:
            symbol_map.setdefault(s, []).append(ex_name)
    common_symbols = [s for s, exs in symbol_map.items() if len(exs) >= 2]
    common_symbols = sorted(common_symbols)[:MAX_COINS]
    print(f"🔍 Выбрано {len(common_symbols)} общих пар /USDT (лимит {MAX_COINS})")

    # для каждой пары проверяем пары бирж
    for symbol in common_symbols:
        ex_list = symbol_map[symbol]
        # переберём пары buy/sell
        for buy_ex in ex_list:
            for sell_ex in ex_list:
                if buy_ex == sell_ex:
                    continue
                try:
                    ask_book = exchanges[buy_ex].fetch_order_book(symbol, limit=5)
                    bid_book = exchanges[sell_ex].fetch_order_book(symbol, limit=5)
                except Exception:
                    continue
                if not ask_book.get('asks') or not bid_book.get('bids'):
                    continue
                ask_price, ask_amt = ask_book['asks'][0]
                bid_price, bid_amt = bid_book['bids'][0]
                if ask_price <= 0:
                    continue
                spread = (bid_price - ask_price) / ask_price
                # объём в USD приблизительно
                vol_buy = ask_price * ask_amt
                vol_sell = bid_price * bid_amt
                approx_vol = max(orderbook_volume_usd(exchanges[buy_ex], symbol), orderbook_volume_usd(exchanges[sell_ex], symbol))
                # фильта по объёму и спреду
                if approx_vol < MIN_VOLUME_USD:
                    continue
                if spread < SPREAD_THRESHOLD:
                    continue
                # сформировать сообщение
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                text = (f"🔥Арбитраж! {symbol}\n"
                        f"Купить: {buy_ex} → {ask_price:.6f}\n"
                        f"Продать: {sell_ex} → {bid_price:.6f}\n"
                        f"СПРЕД: {spread*100:.4f}%\n"
                        f"Объём (USD): {approx_vol:.2f}\n"
                        f"Проверить актуальность: (кнопка ниже)\n"
                        f"Время: {now}")
                print(text)
                # отправляем сигнал всем в whitelist
                await send_signal_to_whitelist(app, text, symbol, buy_ex, sell_ex, spread)
    # конец одной итерации

# ------------------------------
# Запуск бота и планировщик
# ------------------------------
async def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    # регистрируем callback и команды
    app.add_handler(CallbackQueryHandler(check_callback, pattern=r"^check\|"))
    app.add_handler(CommandHandler("add_user", cmd_add_user))
    app.add_handler(CommandHandler("remove_user", cmd_remove_user))
    app.add_handler(CommandHandler("list_users", cmd_list_users))

    # стартуем polling
    await app.bot.set_my_commands([
        ('add_user', 'Добавить пользователя в whitelist (admin/operator)'),
        ('remove_user', 'Удалить пользователя (admin/operator)'),
        ('list_users', 'Показать whitelist (admin/operator)')
    ])
    print("Бот Telegram запущен.")

    # запускаем цикл сканера (в отдельном фоне)
    async def loop():
        while True:
            try:
                await scanner_loop(app)
            except Exception as e:
                print("Ошибка в scanner_loop:", e)
            await asyncio.sleep(CHECK_INTERVAL)

    import asyncio
    # запускаем фоновую задачу сканера и сам polling
    app.create_task(loop())
    await app.initialize()
    await app.start()
    await app.updater.start_polling() if hasattr(app, 'updater') else None
    await app.idle()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
