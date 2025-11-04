import os
import logging
import datetime
from datetime import timezone
from zoneinfo import ZoneInfo
from telegram import Bot
from database import get_upcoming_reminders_window, mark_reminder_sent

# Логирование
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Переменные окружения
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHANNEL_ID_RAW = os.environ.get('TELEGRAM_CHANNEL_ID')
DATABASE_URL = os.environ.get('DATABASE_URL')

if not all([BOT_TOKEN, CHANNEL_ID_RAW, DATABASE_URL]):
    missing = []
    if not BOT_TOKEN: missing.append('TELEGRAM_BOT_TOKEN')
    if not CHANNEL_ID_RAW: missing.append('TELEGRAM_CHANNEL_ID')
    if not DATABASE_URL: missing.append('DATABASE_URL')
    logger.error(f"Missing environment variables: {', '.join(missing)}")
    exit(1)

def main():
    bot = Bot(BOT_TOKEN)
    
    now_utc = datetime.datetime.now(tz=timezone.utc)
    upper_utc = now_utc + datetime.timedelta(hours=24)

    logger.info(f"🔍 GitHub Actions: Checking reminders from {now_utc.isoformat()} to {upper_utc.isoformat()}")

    try:
        reminders = get_upcoming_reminders_window(now_utc, upper_utc)
        logger.info(f"📋 Found {len(reminders)} reminders to process")
    except Exception as e:
        logger.exception(f"❌ Error getting reminders: {e}")
        return

    if not reminders:
        logger.info("✅ No reminders found for next 24 hours.")
        return

    for note in reminders:
        try:
            # Конвертируем время
            if note.reminder_date.tzinfo is None:
                reminder_date_utc = note.reminder_date.replace(tzinfo=timezone.utc)
            else:
                reminder_date_utc = note.reminder_date
                
            reminder_date_local = reminder_date_utc.astimezone(ZoneInfo("Europe/Moscow"))
            display_time = reminder_date_local.strftime('%H:%M %d-%m-%Y')
            
            text = f"🔔 Напоминание: '{note.text}' назначено на {display_time}."
            logger.info(f"📤 Sending reminder for note id={note.id}")
            
            bot.send_message(chat_id=note.user_id, text=text)
            mark_reminder_sent(note.id)
            logger.info(f"✅ GitHub Actions: Reminder note id={note.id} sent successfully")
            
        except Exception as ex:
            logger.exception(f"❌ GitHub Actions: Error sending reminder note id={getattr(note, 'id', '<unknown>')}: {ex}")

if __name__ == '__main__':
    main()