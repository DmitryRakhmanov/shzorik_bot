import os
import re
import logging
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from database import init_db, add_note, get_upcoming_reminders_window, mark_reminder_sent

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загружаем .env
load_dotenv()

# Переменные окружения
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
TELEGRAM_CHANNEL_ID = int(os.environ.get("TELEGRAM_CHANNEL_ID", 0))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
WEBHOOK_PORT = int(os.environ.get("PORT", 10000))
USE_WEBHOOK = os.environ.get("USE_WEBHOOK", "false").lower() in ("true", "1", "t")

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")

if USE_WEBHOOK and not all([WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_PORT]):
    raise ValueError("При USE_WEBHOOK=true, WEBHOOK_URL, WEBHOOK_SECRET_TOKEN и PORT должны быть заданы")

# Инициализация базы данных
init_db()
logger.info("Database initialized.")

# === Парсинг напоминаний ===
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
            return text, " ".join(hashtags), None
    return text, hashtags, reminder_date

# === Обработчики ===
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    cleaned_text, hashtags, reminder_date = parse_reminder(text)
    if "#напоминание" not in hashtags or reminder_date is None:
        await update.message.reply_text("❌ Сообщение должно содержать #напоминание и время в формате @HH:MM DD-MM-YYYY.")
        return
    note = add_note(user_id, cleaned_text, " ".join(hashtags), reminder_date)
    reply = f"✅ Напоминание сохранено: '{note.text}' на {note.reminder_date.astimezone(ZoneInfo('Europe/Moscow')).strftime('%H:%M %d-%m-%Y')}"
    await update.message.reply_text(reply)
    logger.info(f"Saved reminder: {note.text}")

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

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот для напоминаний.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    /start - Приветствие
    /help - Помощь
    /upcoming - Предстоящие напоминания
    """
    await update.message.reply_text(help_text)

async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    notes = get_upcoming_reminders_window(now, now + timedelta(days=30), only_unsent=False)
    if not notes:
        await update.message.reply_text("Нет предстоящих напоминаний.")
        return
    messages = [
        f"🔔 {note.text} - {note.reminder_date.astimezone(ZoneInfo('Europe/Moscow')).strftime('%H:%M %d-%m-%Y')} (отправлено: {'да' if note.reminder_sent else 'нет'})"
        for note in notes
    ]
    await update.message.reply_text("\n".join(messages))

async def check_reminders():
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    upcoming = get_upcoming_reminders_window(now, now + timedelta(days=1))
    for note in upcoming:
        try:
            reminder_date_moscow = note.reminder_date.astimezone(ZoneInfo("Europe/Moscow"))
            await application.bot.send_message(
                chat_id=note.user_id,
                text=f"🔔 Напоминание: '{note.text}' назначено на {reminder_date_moscow.strftime('%H:%M %d-%m-%Y')}"
            )
            mark_reminder_sent(note.id)
            logger.info(f"Sent reminder: {note.text}")
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")

# === Инициализация приложения ===
application = Application.builder().token(BOT_TOKEN).build()
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))
application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.CHANNEL, handle_channel_post))
application.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
application.add_handler(CommandHandler("help", help_command, filters=filters.ChatType.PRIVATE))
application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))

scheduler = AsyncIOScheduler()
scheduler.add_job(check_reminders, "interval", minutes=1)

# === Health-check сервер ===
HEALTH_PORT = WEBHOOK_PORT + 1

async def health_check(request):
    return web.Response(text="OK")

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/healthz", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", HEALTH_PORT)
    await site.start()
    logger.info(f"Health-check server started on port {HEALTH_PORT}")

# === Запуск ===
async def main():
    scheduler.start()
    await start_health_server()

    if USE_WEBHOOK:
        logger.info("Starting bot with webhooks...")
        await application.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path="/telegram",
            webhook_url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET
        )
    else:
        logger.info("Starting bot with polling...")
        await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
