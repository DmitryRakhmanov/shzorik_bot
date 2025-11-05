import os
import re
import logging
import json
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv
from database import init_db, add_note
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
PORT = int(os.environ.get("PORT", 10000))
USE_WEBHOOK = os.environ.get("USE_WEBHOOK", 'true').lower() in ('true', '1', 't')

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not set")
if USE_WEBHOOK and not all([WEBHOOK_URL, WEBHOOK_SECRET]):
    raise ValueError("WEBHOOK_URL and WEBHOOK_SECRET_TOKEN must be set when USE_WEBHOOK=true")

# Инициализация базы данных
init_db()
logger.info("Database initialized.")

# Flask приложение
app = Flask(__name__)

# Инициализация Telegram приложения
application = Application.builder().token(BOT_TOKEN).build()

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
            pass
    return text, " ".join(hashtags), reminder_date

# Обработчик сообщений в канале
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.channel_post:
        user_id = update.channel_post.from_user.id if update.channel_post.from_user else None
        if not user_id:
            logger.warning("No user ID found in channel post")
            return
        text = update.channel_post.text
        cleaned_text, hashtags, reminder_date = parse_reminder(text)
        if "#напоминание" not in hashtags or reminder_date is None:
            logger.info("Invalid reminder format in channel")
            return
        note = add_note(user_id, cleaned_text, hashtags, reminder_date)
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
    /upcoming - Показать предстоящие напоминания на сегодня
    """
    await update.message.reply_text(help_text)

# Команда для просмотра предстоящих напоминаний
async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    notes = get_upcoming_reminders_window(user_id=user_id, start_time=now, end_time=now + timedelta(days=30), only_unsent=False)
    if not notes:
        await update.message.reply_text("Нет предстоящих напоминаний на сегодня.")
        return
    messages = []
    for note in notes:
        reminder_date_moscow = note.reminder_date.astimezone(ZoneInfo("Europe/Moscow"))
        messages.append(f"🔔 {note.text} - назначено на {reminder_date_moscow.strftime('%H:%M %d-%m-%Y')} (отправлено: {'да' if note.reminder_sent else 'нет'})")
    await update.message.reply_text("\n".join(messages))

# Хендлеры
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("upcoming", upcoming_notes_command))
application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.CHANNEL, handle_channel_post))

# Webhook endpoint для Telegram
@app.route('/' + BOT_TOKEN, methods=['POST'])
def webhook():
    if request.headers.get('X-Telegram-Bot-Api-Secret-Token') != WEBHOOK_SECRET:
        return 'Unauthorized', 403
    if request.headers.get('content-type') == 'application/json':
        data = request.get_json()
        update = Update.de_json(data, application.bot)
        application.process_update(update)
    return 'ok'

# Ping endpoint для UptimeRobot
@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "OK"}), 200

if __name__ == "__main__":
    if USE_WEBHOOK:
        # Set webhook asynchronously
        loop = asyncio.get_event_loop()
        loop.run_until_complete(application.bot.set_webhook(
            url=WEBHOOK_URL + '/' + BOT_TOKEN,
            secret_token=WEBHOOK_SECRET
        ))
        logger.info("Webhook set.")
    app.run(host="0.0.0.0", port=PORT)