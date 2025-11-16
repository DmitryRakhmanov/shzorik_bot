# cron.py - Этот файл будет загружен в Yandex Cloud

import os
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Bot

# Импортируем функции из database.py, который также будет загружен
from database import get_upcoming_reminders_window, mark_reminder_sent

# --- Настройка Логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Асинхронная функция для отправки напоминаний ---

async def send_reminders():
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    TZ_NAME = os.environ.get('TZ', 'Europe/Moscow')
    
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        return {'statusCode': 500, 'body': 'Missing token'}
        
    bot = Bot(BOT_TOKEN)
    APP_TZ = ZoneInfo(TZ_NAME)
    
    logger.info("Function started. Checking reminders...")
    
    # Ищем напоминания в окне: now - 1 hour to now + 5 min (в UTC), чтобы ловить задержки
    now_utc = datetime.now(ZoneInfo("UTC"))
    window_start_utc = now_utc - timedelta(hours=1)
    window_end_utc = now_utc + timedelta(minutes=5)
    
    try:
        # Получаем только неотправленные напоминания в окне
        upcoming = get_upcoming_reminders_window(window_start_utc, window_end_utc, only_unsent=True)
        logger.info(f"Found {len(upcoming)} reminders in window.")
    except Exception as e:
        logger.error(f"Error connecting to DB or getting reminders: {e}")
        return {'statusCode': 500, 'body': 'DB Error'}
        
    if not upcoming:
        logger.info("No reminders to send in this window.")
        return {'statusCode': 200, 'body': 'No reminders'}

    # Обработка и отправка напоминаний
    sent_count = 0
    for note in upcoming:
        try:
            # Конвертируем UTC из базы в локальное время для отображения
            reminder_date_local = note.reminder_date.astimezone(APP_TZ)
            
            message_text = f"🔔 Напоминание: «{note.text}» назначено на {reminder_date_local.strftime('%H:%M %d-%m-%Y')}"
            
            # note.user_id - это ID канала или пользователя
            await bot.send_message(
                chat_id=note.user_id,
                text=message_text
            )
            
            # Помечаем как отправленное
            mark_reminder_sent(note.id)
            logger.info(f"Sent reminder {note.id} to {note.user_id}")
            sent_count += 1
            
        except Exception as e:
            logger.error(f"Failed to send reminder {note.id} or mark as sent: {e}")
            
    return {'statusCode': 200, 'body': f'Successfully checked and processed {sent_count} reminders.'}

# --- Основная функция для Yandex Cloud Functions ---

def handler(event, context):
    """
    Основная точка входа для Yandex Cloud Function.
    Запускается по Cron-триггеру.
    """
    return asyncio.run(send_reminders())