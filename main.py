import os
import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from database import init_db, add_note, get_upcoming_reminders_window, mark_reminder_sent

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения из .env
load_dotenv()

# Получение токенов и URL из переменных окружения
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
WEBHOOK_PORT = int(os.environ.get("PORT"))
USE_WEBHOOK = os.environ.get("USE_WEBHOOK", 'false').lower() in ('true', '1', 't')

if not BOT_TOKEN:
    raise ValueError("Не задан TELEGRAM_BOT_TOKEN в .env файле")

# Дополнительная проверка для режима вебхуков
if USE_WEBHOOK and not all([WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_PORT]):
    raise ValueError("При USE_WEBHOOK=true, WEBHOOK_URL, WEBHOOK_SECRET_TOKEN и PORT должны быть заданы")

# Инициализация базы данных
init_db()
logger.info("Database initialized.")

# Парсинг напоминаний из сообщения
def parse_reminder(text: str):
    hashtags = re.findall(r"#\w+", text)
    dt_match = re.search(r"@(\d{2}:\d{2}) (\d{2}-\d{2}-\d{4})", text)
    reminder_date = None
    if dt_match:
        time_str, date_str = dt_match.groups()
        reminder_date = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
        reminder_date = reminder_date.replace(tzinfo=ZoneInfo("Europe/Moscow"))
    return text, " ".join(hashtags), reminder_date

# Обработчик сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    cleaned_text, hashtags_str, reminder_date = parse_reminder(text)
    note = add_note(user_id, cleaned_text, hashtags_str, reminder_date)
    reply = f"✅ Напоминание сохранено: '{note.text}'"
    await update.message.reply_text(reply)
    logger.info(f"Saved reminder: {note.text}")

# Команда для просмотра предстоящих напоминаний
async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    notes = get_upcoming_reminders_window(now, now + timedelta(days=1), only_unsent=False)
    if not notes:
        await update.message.reply_text("Нет предстоящих напоминаний на сегодня.")
        return
    messages = [
        f"🔔 {note.text} - назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}"
        for note in notes
    ]
    await update.message.reply_text("\n".join(messages))

# Проверка напоминаний и отправка пользователю
async def check_reminders():
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    upcoming = get_upcoming_reminders_window(now, now + timedelta(minutes=5))
    for note in upcoming:
        try:
            await application.bot.send_message(
                chat_id=note.user_id,
                text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}"
            )
            mark_reminder_sent(note.id)
            logger.info(f"Sent reminder: {note.text}")
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")

# Инициализация приложения и хендлеров
application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
application.add_handler(CommandHandler("upcoming", upcoming_notes_command))

# Настройка APScheduler
scheduler = AsyncIOScheduler()
scheduler.add_job(check_reminders, "interval", minutes=1)
scheduler.start()

# Запуск бота в зависимости от USE_WEBHOOK
if __name__ == "__main__":
    if USE_WEBHOOK:
        logger.info("Starting bot with webhooks...")
        application.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path=f"/{WEBHOOK_SECRET}",  # Исправлено: теперь путь начинается со слэша
            webhook_url=WEBHOOK_URL + WEBHOOK_SECRET
        )
    else:
        logger.info("Starting bot with polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)