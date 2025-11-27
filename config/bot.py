# bot.py
import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from config import TELEGRAM_TOKEN, SUPPORT_NICK, CHECK_INTERVAL
from scanner import scan_once

scanner_task = None

async def start_scanner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scanner_task
    if scanner_task and not scanner_task.done():
        await update.message.reply_text("Сканер уже запущен!")
        return
    await update.message.reply_text("Сканер запущен!")
    scanner_task = asyncio.create_task(scanner_loop(context))

async def stop_scanner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global scanner_task
    if scanner_task:
        scanner_task.cancel()
        scanner_task = None
        await update.message.reply_text("Сканер остановлен!")
    else:
        await update.message.reply_text("Сканер не был запущен.")

async def scanner_loop(context):
    while True:
        await scan_once()
        await asyncio.sleep(CHECK_INTERVAL)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Старт сканера", callback_data="start")],
        [InlineKeyboardButton("Стоп сканера", callback_data="stop")],
        [InlineKeyboardButton("Поддержка", url=f"https://t.me/{SUPPORT_NICK[1:]}")]
    ])
    await update.message.reply_text("Выберите действие:", reply_markup=keyboard)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "start":
        await start_scanner(update, context)
    elif query.data == "stop":
        await stop_scanner(update, context)

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
