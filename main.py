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
        "Для напоминания используй формат: 'текст заметки #тег #другой_тег @ЧЧ:ММ ДД-ММ-ГГГГ'.\n" # Обновлено в описании
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
    logger.info(f"Получено сообщение от пользователя {user_id}: '{message_text}'")

    # Инициализируем переменные
    hashtags_str = None
    reminder_date = None
    reminder_string_found = None # Будет хранить найденную подстроку напоминания (например, "@01:22 05-07-2025")

    # --- Извлечение напоминания (делаем это первым, чтобы избежать конфликтов) ---
    # ИСПРАВЛЕНО: Поиск полного формата @ЧЧ:ММ ДД-ММ-ГГГГ
    # Теперь regex ожидает время, затем дату.
    full_datetime_pattern = r'\s*@(\d{2}:\d{2})\s+(\d{2}-\d{2}-\d{4})'
    full_datetime_match = re.search(full_datetime_pattern, message_text, re.DOTALL)
    logger.info(f"Результат поиска полного формата даты/времени: {full_datetime_match}")

    if full_datetime_match:
        time_str = full_datetime_match.group(1) # Время теперь в первой группе
        date_str = full_datetime_match.group(2) # Дата теперь во второй группе
        logger.info(f"Найдены время: '{time_str}', дата: '{date_str}'")
        try:
            # ИСПРАВЛЕНО: strptime теперь ожидает время, затем дату
            reminder_date = datetime.strptime(f"{date_str} {time_str}", '%d-%m-%Y %H:%M')
            reminder_string_found = full_datetime_match.group(0) # Сохраняем всю найденную строку
            logger.info(f"Напоминание успешно распарсено: {reminder_date}, найдена строка: '{reminder_string_found}'")
        except ValueError as e:
            logger.error(f"Ошибка парсинга полного формата даты/времени: {e}")
            await update.message.reply_text("Неверный формат даты/времени для напоминания. Используйте @ЧЧ:ММ ДД-ММ-ГГГГ.")
            return
    else:
        # Если полный формат не найден, ищем формат только с датой @ДД-ММ-ГГГГ
        # Этот паттерн остался без изменений, так как он соответствует формату @ДД-ММ-ГГГГ
        date_only_pattern = r'\s*@(\d{2}-\d{2}-\d{4})'
        date_only_match = re.search(date_only_pattern, message_text, re.DOTALL)
        logger.info(f"Результат поиска только даты: {date_only_match}")
        if date_only_match:
            date_str = date_only_match.group(1)
            logger.info(f"Найдена только дата: '{date_str}'")
            try:
                reminder_date = datetime.strptime(date_str, '%d-%m-%Y').replace(hour=9, minute=0)
                reminder_string_found = date_only_match.group(0) # Сохраняем всю найденную строку
                logger.info(f"Напоминание (только дата) успешно распарсено: {reminder_date}, найдена строка: '{reminder_string_found}'")
            except ValueError as e:
                logger.error(f"Ошибка парсинга только даты: {e}")
                await update.message.reply_text("Неверный формат даты для напоминания. Используйте @ДД-ММ-ГГГГ или @ЧЧ:ММ ДД-ММ-ГГГГ.")
                return

    # --- Очистка текста заметки и извлечение хэштегов ---
    # Сначала удаляем часть с напоминанием, если она была найдена
    cleaned_text = message_text
    if reminder_string_found:
        # Удаляем всю найденную строку напоминания, включая @ и окружающие пробелы.
        # Используем re.sub, чтобы гарантировать удаление всех вхождений, если их несколько (хотя и маловероятно)
        cleaned_text = re.sub(re.escape(reminder_string_found), '', cleaned_text).strip()
        logger.info(f"Текст после удаления напоминания: '{cleaned_text}'")
    
    # Извлекаем хэштеги из оставшегося текста
    hashtags = re.findall(r'#(\w+)', cleaned_text)
    hashtags_str = ' '.join(hashtags).lower() if hashtags else None
    logger.info(f"Найденные хэштеги: {hashtags_str}")

    # Удаляем хэштеги из текста, чтобы получить чистый текст заметки
    note_text = re.sub(r'#\w+', '', cleaned_text).strip()
    logger.info(f"Финальный текст заметки: '{note_text}'")

    if not note_text:
        await update.message.reply_text("Пожалуйста, введите текст заметки.")
        return

    # --- Сохранение заметки и ответ ---
    add_note(user_id, note_text, hashtags_str, reminder_date)
    response_text = "Заметка сохранена!"
    if hashtags_str:
        response_text += f"\nХэштеги: {hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response_text += f"\nНапоминание установлено на: {reminder_date.strftime('%H:%M %d-%m-%Y')}"

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
    job_queue.run_repeating(check_reminders, interval=30, first=0) 

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