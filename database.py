import os
import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
#from database import init_db, add_note, get_upcoming_reminders_window, mark_reminder_sent

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
TELEGRAM_CHANNEL_ID = int(os.environ.get("TELEGRAM_CHANNEL_ID", 0))  # Для справки, не обязательно
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
WEBHOOK_PORT = int(os.environ.get("PORT", 10000))
USE_WEBHOOK = os.environ.get("USE_WEBHOOK", 'false').lower() in ('true', '1', 't')

if not BOT_TOKEN:
    raise ValueError("Не задан TELEGRAM_BOT_TOKEN в .env файле")

if USE_WEBHOOK and not all([WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_PORT]):
    raise ValueError("При USE_WEBHOOK=true, WEBHOOK_URL, WEBHOOK_SECRET_TOKEN и PORT должны быть заданы")

# Инициализация базы данных
init_db()
logger.info("Database initialized.")

# Парсинг напоминаний из сообщения
def parse_reminder(text: str):
    # Поддержка кириллицы в хэштегах
    hashtags = re.findall(r"#[а-яА-ЯёЁa-zA-Z0-9_]+", text)
    dt_match = re.search(r"@(\d{2}:\d{2}) (\d{2}-\d{2}-\d{4})", text)
    reminder_date = None
    if dt_match:
        time_str, date_str = dt_match.groups()
        try:
            reminder_date = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
            reminder_date = reminder_date.replace(tzinfo=ZoneInfo("Europe/Moscow"))
        except ValueError:
            return text, " ".join(hashtags), None  # Если формат неверный
    return text, hashtags, reminder_date  # Возвращаем список hashtags для проверки

# Обработчик сообщений в приватном чате (если нужно сохранять из приватных - раскомментируйте добавление хендлера ниже)
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

# Обработчик постов в канале
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    text = update.channel_post.text
    channel_id = update.channel_post.chat.id
    cleaned_text, hashtags, reminder_date = parse_reminder(text)
    if "#напоминание" not in hashtags or reminder_date is None:
        return  # Игнорируем недействительные посты
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
    /upcoming - Показать предстоящие напоминания на ближайшие 30 дней

    Бот работает с каналом: сохраняет сообщения с #напоминание и @HH:MM DD-MM-YYYY, уведомляет за сутки или раньше.
    """
    await update.message.reply_text(help_text)

# Команда для просмотра предстоящих напоминаний (работает в приватном чате)
async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    notes = get_upcoming_reminders_window(now, now + timedelta(days=30), only_unsent=False)  # Расширили на 30 дней
    if not notes:
        await update.message.reply_text("Нет предстоящих напоминаний на ближайшие 30 дней.")
        return
    messages = []
    for note in notes:
        reminder_date_moscow = note.reminder_date.astimezone(ZoneInfo("Europe/Moscow"))
        messages.append(
            f"🔔 {note.text} - назначено на {reminder_date_moscow.strftime('%H:%M %d-%m-%Y')} (отправлено: {'да' if note.reminder_sent else 'нет'})"
        )
    await update.message.reply_text("\n".join(messages))

# Проверка напоминаний и отправка (в канал или пользователю, в зависимости от note.user_id)
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

# Инициализация приложения и хендлеров
application = Application.builder().token(BOT_TOKEN).build()

# Хендлер для приватных сообщений (раскомментируйте, если хотите сохранять напоминания из приватных чатов)
# application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))

# Хендлер для постов в канале
application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.CHANNEL, handle_channel_post))

# Команды (ограничены приватными чатами)
application.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
application.add_handler(CommandHandler("help", help_command, filters=filters.ChatType.PRIVATE))
application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))

# Настройка APScheduler
scheduler = AsyncIOScheduler()
scheduler.add_job(check_reminders, "interval", minutes=1)

# Запуск бота в зависимости от USE_WEBHOOK
if __name__ == "__main__":
    scheduler.start()  # Запускаем здесь, чтобы избежать дубликатов
    if USE_WEBHOOK:
        logger.info("Starting bot with webhooks...")
        application.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path="/telegram",
            webhook_url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET
        )
    else:
        logger.info("Starting bot with polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)