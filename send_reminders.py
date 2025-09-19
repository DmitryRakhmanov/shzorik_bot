# send_reminders.py
import os
import logging
import datetime
from datetime import timezone
from zoneinfo import ZoneInfo

from telegram import Bot
from database import get_upcoming_reminders_window, mark_reminder_sent

# Логирование
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(level=LOG_LEVEL)
logger = logging.getLogger(__name__)

# Переменные окружения
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHANNEL_ID_RAW = os.environ.get('TELEGRAM_CHANNEL_ID')
DATABASE_URL = os.environ.get('DATABASE_URL')
TZ_NAME = os.environ.get('TZ', 'Europe/Moscow')

# Проверка обязательных переменных
missing_vars = []
if not BOT_TOKEN:
    missing_vars.append('TELEGRAM_BOT_TOKEN')
if not CHANNEL_ID_RAW:
    missing_vars.append('TELEGRAM_CHANNEL_ID')
if not DATABASE_URL:
    missing_vars.append('DATABASE_URL')

if missing_vars:
    logger.error("Отсутствуют обязательные переменные окружения: %s", ', '.join(missing_vars))
    raise SystemExit(1)

bot = Bot(BOT_TOKEN)
DISPLAY_TZ = ZoneInfo(TZ_NAME)
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
    now_utc = datetime.datetime.now(tz=UTC)
    upper_utc = now_utc + datetime.timedelta(hours=24)

    logger.info("Проверка напоминаний в окне: %s -> %s", now_utc.isoformat(), upper_utc.isoformat())

    try:
        reminders = get_upcoming_reminders_window(now_utc, upper_utc)
    except Exception as e:
        logger.exception("Ошибка при получении напоминаний: %s", e)
        return

    if not reminders:
        logger.info("Напоминаний на следующие 24 часа не найдено.")
        return

    channel_id = parse_channel_id(CHANNEL_ID_RAW)

    for note in reminders:
        try:
            display_time = to_display_time(note.reminder_date)
            text = f"🔔 Напоминание: '{note.text}' назначено на {display_time}."
            logger.info("Отправка напоминания для note id=%s: %s", note.id, text)
            bot.send_message(chat_id=channel_id, text=text)
            # Пометка как отправленного
            ok = mark_reminder_sent(note.id)
            if ok:
                logger.info("Напоминание note id=%s отмечено как отправленное.", note.id)
            else:
                logger.warning("Не удалось отметить note id=%s как отправленное (не найдено).", note.id)
        except Exception as ex:
            logger.exception("Ошибка при отправке напоминания note id=%s: %s", getattr(note, 'id', '<unknown>'), ex)

if __name__ == '__main__':
    main()
