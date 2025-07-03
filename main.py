import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
import re
from datetime import datetime, timedelta
# Импортируем все необходимые функции из database.py
from database import add_note, find_notes_by_user_and_hashtag, get_upcoming_reminders, get_all_notes_for_user, update_note_reminder_date
import asyncio 
import os
from flask import Flask, request
import threading

# Создаем Flask-приложение для проверки работоспособности (требуется Render.com)
web_app = Flask(__name__)

# Определяем маршрут для проверки работоспособности
@web_app.route('/health')
def health_check():
    """Конечная точка для Render.com, чтобы проверить, работает ли сервис."""
    return 'OK', 200 # Возвращаем "OK" со статусом 200

# Настраиваем логирование, чтобы видеть, что происходит
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# --- Обработчики команд ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение и инструкции по команде /start."""
    user = update.effective_user
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Я бот для заметок. "
        "Чтобы сохранить заметку, просто отправь мне текст. "
        "Для добавления хэштегов используй #хештег. "
        "Для напоминания используй формат: 'текст заметки #тег #другой_тег @ДД-ММ-ГГГГ ЧЧ:ММ'.\n"
        "Напоминания приходят в канал, **за 24 часа до события**.\n"
        "Команды:\n"
        "/find #хештег - найти заметки по хештегу\n"
        "/all_notes - показать все твои заметки\n"
        "/help - показать это сообщение снова"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет справочное сообщение (такое же, как и start)."""
    await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает входящие текстовые сообщения для сохранения в качестве заметок, извлекая хэштеги и напоминания."""
    user_id = update.effective_user.id
    message_text = update.message.text

    # Извлекаем хэштеги
    hashtags = re.findall(r'#(\w+)', message_text)
    hashtags_str = ' '.join(hashtags).lower() if hashtags else None

    # Извлекаем дату и время напоминания (ДД-ММ-ГГГГ ЧЧ:ММ)
    # Регулярное выражение ищет "@ДД-ММ-ГГГГ ЧЧ:ММ"
    reminder_match = re.search(r'@(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2})', message_text)
    reminder_date = None
    if reminder_match:
        # Группа 1: дата (ДД-ММ-ГГГГ), Группа 2: время (ЧЧ:ММ)
        date_str = reminder_match.group(1) 
        time_str = reminder_match.group(2)
        try:
            full_datetime_str = f"{date_str} {time_str}"
            reminder_date = datetime.strptime(full_datetime_str, '%d-%m-%Y %H:%M')
        except ValueError:
            await update.message.reply_text("Неверный формат даты/времени для напоминания. Используйте @ДД-ММ-ГГГГ ЧЧ:ММ.")
            return
    else:
        # Проверяем напоминания только по дате (по умолчанию в 9 утра)
        # Регулярное выражение для извлечения только даты "@ДД-ММ-ГГГГ"
        date_only_match = re.search(r'@(\d{2}-\d{2}-\d{4})', message_text)
        if date_only_match:
            date_str = date_only_match.group(1)
            try:
                reminder_date = datetime.strptime(date_str, '%d-%m-%Y').replace(hour=9, minute=0)
            except ValueError:
                await update.message.reply_text("Неверный формат даты для напоминания. Используйте @ДД-ММ-ГГГГ или @ДД-ММ-ГГГГ ЧЧ:ММ.")
                return

    # Очищаем текст заметки, удаляя хэштеги и части напоминания
    note_text = re.sub(r'#\w+', '', message_text).strip()
    if reminder_match:
        note_text = note_text.replace(reminder_match.group(0), '').strip()
    elif date_only_match:
        note_text = note_text.replace(date_only_match.group(0), '').strip()

    if not note_text:
        await update.message.reply_text("Пожалуйста, введите текст заметки.")
        return

    # Добавляем заметку в базу данных
    add_note(user_id, note_text, hashtags_str, reminder_date)
    response_text = "Заметка сохранена!"
    if hashtags_str:
        response_text += f"\nХэштеги: {hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response_text += f"\nНапоминание установлено на: {reminder_date.strftime('%H:%M %d-%m-%Y')}" # Используем формат ЧЧ:ММ ДД-ММ-ГГГГ

    await update.message.reply_text(response_text)

async def find_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Находит и отображает заметки на основе указанного хэштега."""
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите хэштег для поиска. Пример: /find #важно")
        return

    hashtag = context.args[0].lower()
    if not hashtag.startswith('#'):
        await update.message.reply_text("Хэштег должен начинаться с '#'. Пример: /find #важно")
        return
    
    search_hashtag = hashtag[1:] # Удаляем '#' для поиска в базе данных

    notes = find_notes_by_user_and_hashtag(user_id, search_hashtag)

    if notes:
        response = f"Найденные заметки по хэштегу '{hashtag}':\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (# {note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})" # Используем формат ЧЧ:ММ ДД-ММ-ГГГГ
            response += "\n"
    else:
        response = f"Заметок по хэштегу '{hashtag}' не найдено."

    await update.message.reply_text(response)

async def all_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отображает все заметки, хранящиеся для текущего пользователя."""
    user_id = update.effective_user.id
    notes = get_all_notes_for_user(user_id)

    if notes:
        response = "Все твои заметки:\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (# {note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})" # Используем формат ЧЧ:ММ ДД-ММ-ГГГГ
            response += "\n"
    else:
        await update.message.reply_text("У тебя пока нет заметок.")


# --- Функция проверки напоминаний ---

async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Проверяет базу данных на наличие напоминаний, которые должны сработать
    в течение следующих 24 часов (по времени уведомления),
    и отправляет их в указанный канал Telegram.
    """
    logger.info("Проверка напоминаний...")
    reminders = get_upcoming_reminders()
    
    # Получаем ID канала из переменных окружения.
    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not channel_id:
        logger.error("TELEGRAM_CHANNEL_ID не установлен в переменных окружения. Напоминания не будут отправлены в канал.")
        return

    for note in reminders:
        try:
            # Отправляем напоминание в указанный канал, используя предпочтительный формат даты.
            await context.bot.send_message(
                chat_id=channel_id,
                text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}."
            )
            logger.info(f"Отправлено напоминание в канал {channel_id} для заметки {note.id}")
            
            # Обнуляем reminder_date в базе данных, чтобы напоминание не отправлялось повторно.
            update_note_reminder_date(note.id)
            logger.info(f"Дата напоминания для заметки {note.id} обнулена.")

        except Exception as e:
            logger.error(f"Не удалось отправить напоминание в канал {channel_id}: {e}")


def main() -> None:
    """
    Основная функция для запуска Telegram-бота и Flask-веб-сервера.
    Проверяет наличие переменных окружения и настраивает обработчики бота.
    """
    # Получаем токен Telegram-бота из переменной окружения
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise Exception("TELEGRAM_BOT_TOKEN environment variable is not set! Please set it.")

    # Получаем порт для Flask-сервера из переменной окружения (по умолчанию 10000 для Render)
    PORT = int(os.environ.get("PORT", 10000))

    # Создаем объект Application Telegram-бота
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("find", find_notes_command))
    application.add_handler(CommandHandler("all_notes", all_notes_command))
    
    # Добавляем обработчик для всех текстовых сообщений, которые не являются командами
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Настраиваем JobQueue для повторяющихся задач (например, проверки напоминаний)
    job_queue = application.job_queue
    # Проверяем каждые 300 секунд (5 минут)
    job_queue.run_repeating(check_reminders, interval=300, first=0) 

    # Функция для запуска Flask-веб-сервера в отдельном потоке
    def run_flask_server():
        print(f"Starting Flask web server on port {PORT}...")
        # debug=False и use_reloader=False важны для производственных сред
        web_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

    # Запускаем Flask-сервер в потоке-демоне, чтобы он не блокировал основной поток бота
    flask_thread = threading.Thread(target=run_flask_server)
    flask_thread.daemon = True 
    flask_thread.start()

    print("Starting Telegram bot...")
    # Запускаем бота с использованием long polling.
    # drop_pending_updates=True гарантирует, что любые сообщения, полученные во время
    # оффлайн бота или предыдущей конфликтной сессии, игнорируются при запуске.
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()