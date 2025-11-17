import os
import re
import logging
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
        
    try:
        remind_at = event_date - timedelta(days=1)
        remind_at_utc = remind_at.astimezone(ZoneInfo("UTC"))
        
        text_with_event = f"{cleaned_text} (событие: {event_date.strftime('%H:%M %d-%m-%Y')})"
        
        note = add_note(chat_id, text_with_event, " ".join(hashtags), remind_at_utc)
        
        remind_date_str = remind_at.strftime('%H:%M %d-%m-%Y')
        event_date_str = event_date.strftime('%H:%M %d-%m-%Y')
        reply = f"✅ Напоминание сохранено: «{cleaned_text}»\nБудет уведомлено за сутки ({remind_date_str}) о событии {event_date_str}"
        await update.channel_post.reply_text(reply)
        logger.info(f"Saved reminder for channel {chat_id}: {note.text}")
        
    except Exception as e:
        logger.error(f"Error saving note for channel: {e}")
        await update.channel_post.reply_text(f"❌ Ошибка сохранения: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот для напоминаний. Используйте /upcoming для просмотра.")

async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /upcoming"""
    now_utc = datetime.now(ZoneInfo("UTC"))
    end_of_time = now_utc + timedelta(days=365) # Смотрим на год вперед
    
    try:
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
        logger.error(f"Error fetching upcoming notes: {e}")
        await update.message.reply_text(f"❌ Ошибка получения напоминаний: {e}")

# --- Запуск Бота ---

def main():
    # Создаём Application
    application = Application.builder().token(BOT_TOKEN).build() 
    
    # Хендлер для #напоминание и старого формата
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.CHANNEL, handle_channel_post))
    
    # Хендлер для /notify в канале
    application.add_handler(MessageHandler(filters.Regex(r"^/notify$") & filters.ChatType.CHANNEL, start_notify))
    
    # Команды в ЛС
    application.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))

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