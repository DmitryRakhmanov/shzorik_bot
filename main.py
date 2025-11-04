import os
import re
import logging
import threading
import time
import requests
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from database import init_db, add_note, get_upcoming_reminders_window, mark_reminder_sent

# ---------------------------------------------------
# НАСТРОЙКА ЛОГОВ
# ---------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------
# ЗАГРУЗКА .env
# ---------------------------------------------------
load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
WEBHOOK_PORT = int(os.environ.get("PORT", 10000))
USE_WEBHOOK = os.environ.get("USE_WEBHOOK", 'false').lower() in ('true', '1', 't')

if not BOT_TOKEN:
    raise ValueError("❌ Не задан TELEGRAM_BOT_TOKEN в .env файле")

if USE_WEBHOOK and not all([WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_PORT]):
    raise ValueError("❌ При USE_WEBHOOK=true нужно указать WEBHOOK_URL, WEBHOOK_SECRET_TOKEN и PORT")

# ---------------------------------------------------
# ИНИЦИАЛИЗАЦИЯ
# ---------------------------------------------------
init_db()
logger.info("✅ Database initialized.")

application = Application.builder().token(BOT_TOKEN).build()

# ---------------------------------------------------
# ФУНКЦИЯ САМОПИНГА (ЧТОБЫ RENDER НЕ ЗАСЫПАЛ)
# ---------------------------------------------------
def keep_alive():
    """Периодически пингует Render, чтобы бот не засыпал."""
    while True:
        try:
            if WEBHOOK_URL:
                url = WEBHOOK_URL.split("/telegram")[0]  # пингуем корень сайта
                response = requests.get(url)
                logger.info(f"✅ Self-ping OK ({response.status_code})")
        except Exception as e:
            logger.warning(f"❌ Self-ping error: {e}")
        time.sleep(600)  # каждые 10 минут

threading.Thread(target=keep_alive, daemon=True).start()

# ---------------------------------------------------
# ОБРАБОТЧИКИ КОМАНД
# ---------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот-напоминалка.\n"
        "Отправь мне текст с датой и временем, например:\n\n"
        "`Позвонить клиенту завтра в 14:00`\n\n"
        "И я напомню тебе вовремя 💬",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📘 Команды:\n"
        "/start — начать работу\n"
        "/help — помощь\n"
        "Просто напиши сообщение с датой и временем, и я создам напоминание."
    )

# ---------------------------------------------------
# ОБРАБОТКА СООБЩЕНИЙ (добавление напоминания)
# ---------------------------------------------------
async def add_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text.strip()
    now = datetime.now(ZoneInfo("Europe/Moscow"))

    # Пытаемся распознать дату и время (примеры: "завтра в 14:30", "через 10 минут")
    match = re.search(r"(\d{1,2})[:.](\d{2})", text)
    if not match:
        await update.message.reply_text("⏰ Не вижу времени в сообщении. Пример: 'завтра в 14:00'")
        return

    hour, minute = int(match.group(1)), int(match.group(2))
    reminder_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reminder_time < now:
        reminder_time += timedelta(days=1)

    add_note(user_id, text, reminder_time)
    await update.message.reply_text(f"✅ Напоминание создано на {reminder_time:%d.%m %H:%M}")

# ---------------------------------------------------
# ПРОВЕРКА И ОТПРАВКА НАПОМИНАНИЙ
# ---------------------------------------------------
async def check_reminders():
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    end_time = now + timedelta(minutes=1)  # 🔧 добавлено
    reminders = get_upcoming_reminders_window(now, end_time)

    for reminder in reminders:
        reminder_id, user_id, text, remind_at = reminder
        try:
            await application.bot.send_message(user_id, f"🔔 Напоминание: {text}")
            mark_reminder_sent(reminder_id)
        except Exception as e:
            logger.warning(f"Ошибка при отправке напоминания пользователю {user_id}: {e}")


# ---------------------------------------------------
# НАСТРОЙКА SCHEDULER
# ---------------------------------------------------
scheduler = AsyncIOScheduler()
scheduler.add_job(lambda: asyncio.run(check_reminders()), "interval", minutes=1)

# ---------------------------------------------------
# РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ
# ---------------------------------------------------
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_reminder))

# ---------------------------------------------------
# ЗАПУСК БОТА
# ---------------------------------------------------
if __name__ == "__main__":
    scheduler.start()

    if USE_WEBHOOK:
        logger.info("🚀 Starting bot with webhooks...")
        application.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path="/telegram",
            webhook_url=f"{WEBHOOK_URL}/telegram",
            secret_token=WEBHOOK_SECRET
        )
    else:
        logger.info("🚀 Starting bot with polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)
