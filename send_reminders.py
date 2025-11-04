import os
import logging
import datetime
from datetime import timezone
from zoneinfo import ZoneInfo
from telegram import Bot
from database import get_upcoming_reminders_window, mark_reminder_sent

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')

if not all([BOT_TOKEN, DATABASE_URL]):
    logger.error("Missing required environment variables")
    exit(1)

def main():
    bot = Bot(BOT_TOKEN)
    now_utc = datetime.datetime.now(tz=timezone.utc)
    upper_utc = now_utc + datetime.timedelta(hours=24)

    logger.info(f"GitHub Actions: Checking reminders from {now_utc} to {upper_utc}")

    try:
        reminders = get_upcoming_reminders_window(now_utc, upper_utc)
        logger.info(f"Found {len(reminders)} reminders")
    except Exception as e:
        logger.error(f"Error: {e}")
        return

    for note in reminders:
        try:
            if note.reminder_date.tzinfo is None:
                reminder_date_utc = note.reminder_date.replace(tzinfo=timezone.utc)
            else:
                reminder_date_utc = note.reminder_date
                
            reminder_date_local = reminder_date_utc.astimezone(ZoneInfo("Europe/Moscow"))
            display_time = reminder_date_local.strftime('%d.%m.%Y в %H:%M')
            
            text = f"🔔 Напоминание: «{note.text}» запланировано на {display_time}!"
            
            bot.send_message(chat_id=note.user_id, text=text)
            mark_reminder_sent(note.id)
            logger.info(f"GitHub: Sent reminder {note.id}")
            
        except Exception as ex:
            logger.error(f"GitHub: Error sending {note.id}: {ex}")

if __name__ == '__main__':
    main()