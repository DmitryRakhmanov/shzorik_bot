# cron_function.py - Этот файл будет загружен в Yandex Cloud

import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Bot

# Импортируем функции из database.py, который также будет загружен
from database import get_upcoming_reminders_window, mark_reminder_sent

# --- Настройка Логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Основная функция для Yandex Cloud Functions ---

def handler(event, context):
    """
    Основная точка входа для Yandex Cloud Function.
    Запускается по Cron-триггеру.
    """
    
    # Переменные окружения будут переданы из настроек Yandex Cloud
    BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
    TZ_NAME = os.environ.get('TZ', 'Europe/Moscow')
    
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        return {'statusCode': 500, 'body': 'Missing token'}
        
    bot = Bot(BOT_TOKEN)
    APP_TZ = ZoneInfo(TZ_NAME)
    
    logger.info("Function started. Checking reminders...")
    
    # Ищем напоминания, которые должны сработать в ближайшие 24 часа (в UTC)
    now_utc = datetime.now(ZoneInfo("UTC"))
    window_end_utc = now_utc + timedelta(hours=24)
    
    try:
        # Получаем только неотправленные напоминания
        upcoming = get_upcoming_reminders_window(now_utc, window_end_utc, only_unsent=True)
    except Exception as e:
        logger.error(f"Error connecting to DB or getting reminders: {e}")
        return {'statusCode': 500, 'body': 'DB Error'}
        
    if not upcoming:
        logger.info("No reminders to send in this window.")
        return {'statusCode': 200, 'body': 'No reminders'}

    # Обработка и отправка напоминаний
    for note in upcoming:
        try:
            # Конвертируем UTC из базы в локальное время для отображения
            reminder_date_local = note.reminder_date.astimezone(APP_TZ)
            
            message_text = f"🔔 Напоминание: «{note.text}» назначено на {reminder_date_local.strftime('%H:%M %d-%m-%Y')}"
            
            # note.user_id - это ID канала или пользователя
            bot.send_message(
                chat_id=note.user_id,
                text=message_text
            )
            
            # Помечаем как отправленное
            mark_reminder_sent(note.id)
            logger.info(f"Sent reminder {note.id} to {note.user_id}")
            
        except Exception as e:
            logger.error(f"Failed to send reminder {note.id} or mark as sent: {e}")
            
    return {'statusCode': 200, 'body': f'Successfully checked and processed {len(upcoming)} reminders.'}