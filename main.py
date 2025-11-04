import os
import re
import logging
import asyncio
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv
from database import init_db, add_note, get_upcoming_reminders_window
from flask import Flask, jsonify

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения из .env
load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID")
PORT = int(os.environ.get("PORT", 10000))

if not BOT_TOKEN:
    raise ValueError("Не задан TELEGRAM_BOT_TOKEN в .env файле")

# Инициализация базы данных
init_db()
logger.info("Database initialized.")

# Flask приложение для пинга
app = Flask(__name__)

@app.route('/')
def home():
    return "Reminder Bot is running with polling!"

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "OK", "timestamp": datetime.now().isoformat()}), 200

# Парсинг напоминаний из сообщения
def parse_reminder(text: str):
    hashtags = re.findall(r"#[а-яА-ЯёЁa-zA-Z0-9_]+", text)
    dt_match = re.search(r"@(\d{2}:\d{2}) (\d{2}-\d{2}-\d{4})", text)
    reminder_date = None
    if dt_match:
        time_str, date_str = dt_match.groups()
        try:
            reminder_date = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
            reminder_date = reminder_date.replace(tzinfo=ZoneInfo("Europe/Moscow"))
        except ValueError:
            return text, hashtags, None
    return text, hashtags, reminder_date

# Обработчик постов в канале
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    text = update.channel_post.text
    channel_id = update.channel_post.chat.id
    cleaned_text, hashtags, reminder_date = parse_reminder(text)
    if "#напоминание" not in hashtags or reminder_date is None:
        return
    note = add_note(channel_id, cleaned_text, " ".join(hashtags), reminder_date)
    reply = f"✅ Напоминание сохранено: '{note.text}' на {note.reminder_date.astimezone(ZoneInfo('Europe/Moscow')).strftime('%H:%M %d-%m-%Y')}"
    await update.channel_post.reply_text(reply)
    logger.info(f"Saved reminder from channel: {note.text}")

# Команда /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот для напоминаний. Я сохраняю напоминания из канала и уведомляю о них. Используйте /upcoming для просмотра предстоящих напоминаний. Для помощи используйте /help.")

# Команда /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    Доступные команды:
    /start - Приветствие и начало работы
    /help - Показать эту помощь
    /upcoming - Показать предстоящие напоминания

    Бот работает с каналом: сохраняет сообщения с #напоминание и @HH:MM DD-MM-YYYY, уведомляет за сутки.
    """
    await update.message.reply_text(help_text)

# Команда для просмотра предстоящих напоминаний
async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    notes = get_upcoming_reminders_window(now, now + timedelta(days=30), only_unsent=False)
    
    # Фильтруем заметки только для текущего пользователя
    user_notes = [note for note in notes if note.user_id == user_id]
    
    if not user_notes:
        await update.message.reply_text("Нет предстоящих напоминаний.")
        return
        
    messages = []
    for note in user_notes:
        reminder_date_moscow = note.reminder_date.astimezone(ZoneInfo("Europe/Moscow"))
        status = "✅ отправлено" if note.reminder_sent else "⏳ ожидает"
        messages.append(f"🔔 {note.text}\n📅 {reminder_date_moscow.strftime('%H:%M %d-%m-%Y')} ({status})")
    
    await update.message.reply_text("\n\n".join(messages))

# Запуск Flask в отдельном потоке
def run_flask():
    """Запуск Flask сервера в отдельном потоке"""
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# Основная асинхронная функция для запуска бота
async def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Хендлеры
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.CHANNEL, handle_channel_post))
    application.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("help", help_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))

    logger.info("Starting bot with polling...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    logger.info("Starting application...")
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("Flask started in separate thread")
    
    # Запускаем бота в основном потоке
    logger.info("Starting bot...")
    asyncio.run(main())