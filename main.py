import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
import re
from datetime import datetime, timedelta
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
    return 'OK', 200

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# --- Обработчики команд ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    # Используем update.effective_message.reply_html для ответа в тот же чат (личный или канал)
    await update.effective_message.reply_html(
        f"Привет, {user.mention_html()}! Я бот для заметок. "
        "Чтобы сохранить заметку, просто отправь мне текст. "
        "Для добавления хэштегов используй #хештег. "
        "Для напоминания используй формат: 'текст заметки #тег #другой_тег @ЧЧ:ММ ДД-ММ-ГГГГ'.\n"
        "Напоминания приходят в канал, **за 24 часа до события**.\n"
        "Команды:\n"
        "/find #хештег - найти заметки по хештегу (для этого пользователя)\n"
        "/all_notes - показать все твои заметки (для этого пользователя)\n"
        "/upcoming_notes - показать все предстоящие напоминания (для канала)\n"
        "/help - показать это сообщение снова"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Добавляем логирование для отладки источника сообщения
    if update.message:
        logger.info(f"Сообщение получено из личного/группового чата. User ID: {update.message.from_user.id}, Chat ID: {update.message.chat_id}")
        message_obj = update.message
    elif update.channel_post:
        logger.info(f"Сообщение получено из канала. Channel ID: {update.channel_post.chat_id}")
        message_obj = update.channel_post
    else:
        logger.warning("Получено обновление, но ни message, ни channel_post не найдены.")
        return

    user_id = message_obj.from_user.id if message_obj.from_user else None
    # Если это канал, from_user может быть None, или user_id может быть неактуален для логики канала
    # Для сохранения заметки, мы все равно используем user_id отправителя, если он есть
    # Или можно использовать chat_id канала, если заметки должны быть 'привязаны' к каналу, а не к пользователю.
    # Но для ваших задач, user_id отправителя пока подходит.
    
    message_text = message_obj.text
    logger.info(f"Получено сообщение от пользователя {user_id} (если есть) / из чата {message_obj.chat_id}: '{message_text}'")

    hashtags_str = None
    reminder_date = None
    reminder_string_found = None

    full_datetime_pattern = r'\s*@(\d{2}:\d{2})\s+(\d{2}-\d{2}-\d{4})'
    full_datetime_match = re.search(full_datetime_pattern, message_text, re.DOTALL)
    logger.info(f"Результат поиска полного формата даты/времени: {full_datetime_match}")

    if full_datetime_match:
        time_str = full_datetime_match.group(1)
        date_str = full_datetime_match.group(2)
        logger.info(f"Найдены время: '{time_str}', дата: '{date_str}'")
        try:
            reminder_date = datetime.strptime(f"{date_str} {time_str}", '%d-%m-%Y %H:%M')
            reminder_string_found = full_datetime_match.group(0)
            logger.info(f"Напоминание успешно распарсено: {reminder_date}, найдена строка: '{reminder_string_found}'")
        except ValueError as e:
            logger.error(f"Ошибка парсинга полного формата даты/времени: {e}")
            await message_obj.reply_text("Неверный формат даты/времени для напоминания. Используйте @ЧЧ:ММ ДД-ММ-ГГГГ.")
            return
    else:
        date_only_pattern = r'\s*@(\d{2}-\d{2}-\d{4})'
        date_only_match = re.search(date_only_pattern, message_text, re.DOTALL)
        logger.info(f"Результат поиска только даты: {date_only_match}")
        if date_only_match:
            date_str = date_only_match.group(1)
            logger.info(f"Найдена только дата: '{date_str}'")
            try:
                reminder_date = datetime.strptime(date_str, '%d-%m-%Y').replace(hour=9, minute=0)
                reminder_string_found = date_only_match.group(0)
                logger.info(f"Напоминание (только дата) успешно распарсено: {reminder_date}, найдена строка: '{reminder_string_found}'")
            except ValueError as e:
                logger.error(f"Ошибка парсинга только даты: {e}")
                await message_obj.reply_text("Неверный формат даты для напоминания. Используйте @ДД-ММ-ГГГГ или @ЧЧ:ММ ДД-ММ-ГГГГ.")
                return

    cleaned_text = message_text
    if reminder_string_found:
        cleaned_text = re.sub(re.escape(reminder_string_found), '', cleaned_text).strip()
        logger.info(f"Текст после удаления напоминания: '{cleaned_text}'")
    
    hashtags = re.findall(r'#(\w+)', cleaned_text)
    hashtags_str = ' '.join(hashtags).lower() if hashtags else None
    logger.info(f"Найденные хэштеги: {hashtags_str}")

    note_text = re.sub(r'#\w+', '', cleaned_text).strip()
    logger.info(f"Финальный текст заметки: '{note_text}'")

    if not note_text:
        await message_obj.reply_text("Пожалуйста, введите текст заметки.")
        return

    # User_id здесь - это ID пользователя, который отправил сообщение.
    # Если нужно, чтобы заметки в канале привязывались к ID канала, то user_id = message_obj.chat_id
    # Но для вашей текущей логики (all_notes для пользователя, upcoming_notes для всех), from_user.id подходит.
    add_note(user_id, note_text, hashtags_str, reminder_date)
    response_text = "Заметка сохранена!"
    if hashtags_str:
        response_text += f"\nХэштеги: {hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response_text += f"\nНапоминание установлено на: {reminder_date.strftime('%H:%M %d-%m-%Y')}"

    await message_obj.reply_text(response_text)


# Функции команд также должны использовать effective_message для ответа
async def find_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message_obj = update.effective_message # Использование effective_message
    if not context.args:
        await message_obj.reply_text("Пожалуйста, укажите хэштег для поиска. Пример: /find #важно")
        return

    hashtag = context.args[0].lower()
    if not hashtag.startswith('#'):
        await message_obj.reply_text("Хэштег должен начинаться с '#'. Пример: /find #важно")
        return
    
    search_hashtag = hashtag[1:]

    notes = find_notes_by_user_and_hashtag(user_id, search_hashtag)

    if notes:
        response = f"Найденные заметки по хэштегу '{hashtag}':\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (# {note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})"
            response += "\n"
    else:
        response = f"Заметок по хэштегу '{hashtag}' не найдено."

    await message_obj.reply_text(response)

async def all_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message_obj = update.effective_message # Использование effective_message
    notes = get_all_notes_for_user(user_id)

    if notes:
        response = "Все твои заметки:\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (# {note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})"
            response += "\n"
    else:
        await message_obj.reply_text("У тебя пока нет заметок.")

async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Вызвана команда /upcoming_notes.")
    message_obj = update.effective_message # Использование effective_message
    notes = get_upcoming_reminders()

    if notes:
        response = "📅 Предстоящие напоминания:\n"
        for i, note in enumerate(notes):
            if note.reminder_date:
                formatted_date = note.reminder_date.strftime('%H:%M %d-%m-%Y')
                response += f"{i+1}. {note.text} (Напоминание: {formatted_date})"
                if note.hashtags:
                    response += f" (# {note.hashtags.replace(' ', ', #')})"
                response += "\n"
        if len(response) > 4000:
             response = response[:3900] + "\n... (список обрезан, слишком много заметок)"
    else:
        response = "На данный момент нет предстоящих напоминаний."

    await message_obj.reply_text(response) # Отвечаем в тот же чат, откуда пришла команда

# --- Функция проверки напоминаний ---

async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Проверка напоминаний...")
    reminders = get_upcoming_reminders()
    
    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not channel_id:
        logger.error("TELEGRAM_CHANNEL_ID не установлен в переменных окружения. Напоминания не будут отправлены в канал.")
        return

    for note in reminders:
        try:
            if note.reminder_date:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}."
                )
                logger.info(f"Отправлено напоминание в канал {channel_id} для заметки {note.id}")
                
                update_note_reminder_date(note.id)
                logger.info(f"Дата напоминания для заметки {note.id} обнулена.")

        except Exception as e:
            logger.error(f"Не удалось отправить напоминание в канал {channel_id}: {e}")


def main() -> None:
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise Exception("TELEGRAM_BOT_TOKEN environment variable is not set! Please set it.")

    PORT = int(os.environ.get("PORT", 10000))

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("find", find_notes_command))
    application.add_handler(CommandHandler("all_notes", all_notes_command))
    application.add_handler(CommandHandler("upcoming_notes", upcoming_notes_command))
    
    # *** ИЗМЕНЕНИЯ ЗДЕСЬ ***
    # Обработчик для текстовых сообщений (личные чаты, группы)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # ДОПОЛНИТЕЛЬНЫЙ обработчик для текстовых постов в каналах
    application.add_handler(MessageHandler(filters.CHANNEL_POST & filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Обработчики команд для каналов (они также должны реагировать на команды в channel_post)
    # CommandHandler автоматически проверяет update.effective_message, но явное добавление фильтра ChannelPost
    # для уверенности, если команды не работали.
    application.add_handler(CommandHandler("start", start, filters=filters.COMMAND & filters.CHANNEL_POST))
    application.add_handler(CommandHandler("help", help_command, filters=filters.COMMAND & filters.CHANNEL_POST))
    application.add_handler(CommandHandler("find", find_notes_command, filters=filters.COMMAND & filters.CHANNEL_POST))
    application.add_handler(CommandHandler("all_notes", all_notes_command, filters=filters.COMMAND & filters.CHANNEL_POST))
    application.add_handler(CommandHandler("upcoming_notes", upcoming_notes_command, filters=filters.COMMAND & filters.CHANNEL_POST))


    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=300, first=0) 

    def run_flask_server():
        print(f"Starting Flask web server on port {PORT}...")
        web_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

    flask_thread = threading.Thread(target=run_flask_server)
    flask_thread.daemon = True 
    flask_thread.start()

    print("Starting Telegram bot...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()