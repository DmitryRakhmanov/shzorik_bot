# cron.py — файл для Yandex Cloud Function

import os
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Bot

# Импортируем функции из database.py
from database import get_upcoming_reminders_window, mark_reminder_sent


# --- Логирование ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# --- Основная асинхронная задача ---
async def send_reminders():
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    TZ_NAME = os.environ.get("TZ", "Europe/Moscow")

    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set")
        return {"statusCode": 500, "body": "Missing bot token"}

    bot = Bot(BOT_TOKEN)
    APP_TZ = ZoneInfo(TZ_NAME)

    logger.info("Cron started. Checking reminders...")

    # Текущее время в UTC
    now_utc = datetime.now(ZoneInfo("UTC"))

    # Окно: задержка -20 мин, +5 мин вперёд
    window_start_utc = now_utc - timedelta(minutes=20)
    window_end_utc = now_utc + timedelta(minutes=5)

    try:
        upcoming = get_upcoming_reminders_window(
            window_start_utc, window_end_utc, only_unsent=True
        )
    except Exception as e:
        logger.error(f"DB error: {e}")
        return {"statusCode": 500, "body": "DB error"}

    logger.info(f"Found {len(upcoming)} reminders")

    if not upcoming:
        return {"statusCode": 200, "body": "No reminders in window"}

    sent_count = 0

    for note in upcoming:
        try:
            # Конвертация UTC → локальное время
            local_dt = note.reminder_date.astimezone(APP_TZ)

            # ВАЖНО: новый формат сообщения
            message_text = (
                f"🔔 Напоминание:\n"
                f"«{note.text}»\n"
                f"Время события: {local_dt.strftime('%H:%M')}"
            )

            await bot.send_message(chat_id=note.user_id, text=message_text)

            # Отмечаем как отправленное
            mark_reminder_sent(note.id)
            logger.info(f"Sent reminder {note.id} to {note.user_id}")
            sent_count += 1

        except Exception as e:
            logger.error(f"Failed to send reminder {note.id}: {e}")

    return {"statusCode": 200, "body": f"Sent {sent_count} reminders"}


# --- Точка входа ---
def handler(event, context):
    """
    Запускается по cron в Yandex Cloud Function.
    """
    return asyncio.run(send_reminders())
