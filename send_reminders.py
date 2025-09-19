```python
# send_reminders.py
import os
import logging
import datetime
from datetime import timezone
from zoneinfo import ZoneInfo

from telegram import Bot
from database import get_upcoming_reminders_window, mark_reminder_sent

# Логирование
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Параметры из окружения
BOT_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHANNEL_ID_RAW = os.environ.get('TELEGRAM_CHANNEL_ID')  # '-100...' или '@channelusername'

if not BOT_TOKEN:
    logger.error("TELEGRAM_TOKEN environment variable is not set")
    raise SystemExit(1)
if not CHANNEL_ID_RAW:
    logger.error("TELEGRAM_CHANNEL_ID environment variable is not set")
    raise SystemExit(1)

bot = Bot(BOT_TOKEN)

# Таймзоны
tz_name = os.environ.get('TZ', 'Europe/Moscow')  # по умолчанию Europe/Moscow (UTC+3)
DISPLAY_TZ = ZoneInfo(tz_name)  # для показа времени пользователю
UTC = timezone.utc

def parse_channel_id(raw: str):
    """
    Возвращает int если raw выглядит как числовой id (-100...), иначе строку (например @channelusername).
    """
    raw = raw.strip()
    if raw.startswith('@'):
        return raw
    try:
        return int(raw)
    except ValueError:
        return raw

def to_display_time(dt_utc):
    """
    Принимает datetime (может быть naive или aware). Возвращает строку времени в DISPLAY_TZ.
    """
    if dt_utc is None:
        return ""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    dt_local = dt_utc.astimezone(DISPLAY_TZ)
    return dt_local.strftime('%H:%M %d-%m-%Y')

def main():
    # окно проверки: сейчас (UTC) .. +24 часа
    now_utc = datetime.datetime.now(tz=UTC)
    upper_utc = now_utc + datetime.timedelta(hours=24)

    logger.info("Checking reminders in window: %s -> %s", now_utc.isoformat(), upper_utc.isoformat())

    # Получаем заметки из database.get_upcoming_reminders_window
    reminders = get_upcoming_reminders_window(now_utc, upper_utc)

    if not reminders:
        logger.info("No reminders found in the next 24 hours.")
        return

    channel_id = parse_channel_id(CHANNEL_ID_RAW)

    for note in reminders:
        try:
            display_time = to_display_time(note.reminder_date)
            text = f"🔔 Напоминание: '{note.text}' назначено на {display_time}."
            logger.info("Sending reminder for note id=%s: %s", note.id, text)
            bot.send_message(chat_id=channel_id, text=text)
            # Помечаем как отправленное
            ok = mark_reminder_sent(note.id)
            if ok:
                logger.info("Marked note id=%s as sent.", note.id)
            else:
                logger.warning("Could not mark note id=%s as sent (note not found).", note.id)
        except Exception as ex:
            logger.exception("Failed to send reminder for note id=%s: %s", getattr(note, 'id', '<unknown>'), ex)

if __name__ == '__main__':
    main()
```