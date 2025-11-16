import os
import re
import logging
import asyncio  # Новый импорт для Queue
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from dotenv import load_dotenv

from database import init_db, add_note, get_upcoming_reminders_window

# --- Настройка Логирования и Конфигурации ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
load_dotenv() 

# Переменные окружения для Webhook
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
WEBHOOK_PORT = int(os.environ.get("PORT", 10000))
TZ_NAME = os.environ.get("TZ", "Europe/Moscow") 
APP_TZ = ZoneInfo(TZ_NAME)

if not all([BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET]):
    raise ValueError("Не заданы все переменные для Webhook (BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET_TOKEN)")

# Автоматическое исправление WEBHOOK_URL: добавляем /telegram, если нет
if not WEBHOOK_URL.endswith("/telegram"):
    WEBHOOK_URL = WEBHOOK_URL.rstrip("/") + "/telegram"
    logger.info(f"Автоматически скорректирован WEBHOOK_URL: {WEBHOOK_URL}")

# --- Инициализация БД ---
try:
    init_db()
    logger.info("Database initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize database: {e}. Exiting.")
    exit(1)

# --- Вспомогательные функции ---

def parse_reminder(text: str):
    hashtags = re.findall(r"#[а-яА-ЯёЁa-zA-Z0-9_]+", text)
    dt_match = re.search(r"@(\d{2}:\d{2}) (\d{2}-\d{2}-\d{4})", text)
    event_date = None
    
    if dt_match:
        time_str, date_str = dt_match.groups()
        try:
            naive_dt = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
            event_date = naive_dt.replace(tzinfo=APP_TZ)
        except ValueError:
            return text, " ".join(hashtags), None
            
    cleaned_text = re.sub(r"#[а-яА-ЯёЁa-zA-Z0-9_]+", "", text).strip()
    if dt_match:
        cleaned_text = cleaned_text.replace(dt_match.group(0), "").strip()
        
    return cleaned_text, " ".join(hashtags), event_date

# --- Хендлеры сообщений и команд ---

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received channel post update: {update.to_dict()}")  # Лог для отладки
    try:
        if not update.channel_post or not update.channel_post.text:
            return
            
        text = update.channel_post.text
        chat_id = update.channel_post.chat.id
        
        cleaned_text, hashtags, event_date = parse_reminder(text)
        
        if "#напоминание" not in hashtags or event_date is None:
            logger.info("Ignoring post: no #напоминание or valid date found.")
            return
        
        now = datetime.now(APP_TZ)
        if event_date < now + timedelta(days=1):
            await update.channel_post.reply_text("❌ Дата события должна быть хотя бы через сутки.")
            return
            
        remind_at = event_date - timedelta(days=1)
        remind_at_utc = remind_at.astimezone(ZoneInfo("UTC"))
        
        text_with_event = f"{cleaned_text} (событие: {event_date.strftime('%H:%M %d-%m-%Y')})"
        
        note = add_note(chat_id, text_with_event, hashtags, remind_at_utc)
        
        remind_date_str = remind_at.strftime('%H:%M %d-%m-%Y')
        event_date_str = event_date.strftime('%H:%M %d-%m-%Y')
        reply = f"✅ Напоминание сохранено: «{cleaned_text}»\nБудет уведомлено за сутки ({remind_date_str}) о событии {event_date_str}"
        await update.channel_post.reply_text(reply)
        logger.info(f"Saved reminder for channel {chat_id}: {note.text}")
        
    except Exception as e:
        logger.error(f"Error in handle_channel_post: {e}")
        if update.channel_post:
            await update.channel_post.reply_text(f"❌ Ошибка: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received /start update: {update.to_dict()}")
    try:
        await update.message.reply_text("Привет! Я бот для напоминаний. Используйте /upcoming для просмотра предстоящих напоминаний.")
    except Exception as e:
        logger.error(f"Error in start_command: {e}")

async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received /upcoming update: {update.to_dict()}")
    try:
        now_utc = datetime.now(ZoneInfo("UTC"))
        end_of_time = now_utc + timedelta(days=365)
        
        notes = get_upcoming_reminders_window(now_utc, end_of_time, only_unsent=True)
        
        if not notes:
            await update.message.reply_text("Нет предстоящих неотправленных напоминаний.")
            return
            
        messages = ["🔔 Предстоящие напоминания:"]
        for note in notes:
            reminder_date_local = note.reminder_date.astimezone(APP_TZ)
            messages.append(
                f"• «{note.text}» - {reminder_date_local.strftime('%H:%M %d-%m-%Y')}"
            )
        await update.message.reply_text("\n".join(messages[:15])) 
        
    except Exception as e:
        logger.error(f"Error in upcoming_notes_command: {e}")
        await update.message.reply_text(f"❌ Ошибка получения напоминаний: {e}")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"Received echo update in private chat: {update.to_dict()}")
    try:
        await update.message.reply_text(f"Echo: {update.message.text}")
    except Exception as e:
        logger.error(f"Error in echo: {e}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception while handling an update: {context.error}")

# --- Запуск Бота ---

def main():
    # ИСПРАВЛЕНИЕ: Создаём реальную asyncio.Queue для update_queue (решает NoneType ошибки и 500 Internal Server Error)
    update_queue = asyncio.Queue()
    
    application = Application.builder().token(BOT_TOKEN).update_queue(update_queue).build()
    
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.CHANNEL, handle_channel_post))
    application.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("help", start_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, echo))
    application.add_error_handler(error_handler)
    
    logger.info(f"Using WEBHOOK_URL: {WEBHOOK_URL}")
    logger.info("Starting bot with webhooks...")
    application.run_webhook(
        listen="0.0.0.0",
        port=WEBHOOK_PORT,
        url_path="/telegram",
        webhook_url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )

if __name__ == "__main__":
    main()