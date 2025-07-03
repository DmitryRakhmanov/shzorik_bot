# main.py
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
import re
from datetime import datetime, timedelta
from database import add_note, find_notes_by_user_and_hashtag, get_upcoming_reminders, get_all_notes_for_user
import asyncio
import os
from flask import Flask, request

# Создаем Flask-приложение
web_app = Flask(__name__)

# Определяем маршрут /health, который Render будет "пинговать"
@web_app.route('/health')
def health_check():
    return 'OK', 200 # Просто возвращаем "OK" и статус 200 (успешно)

# Настройка логирования, чтобы видеть, что происходит
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logging.getLogger(__name__).setLevel(logging.INFO) # Можно установить DEBUG для более подробной информации
logger = logging.getLogger(__name__)

# --- Обработчики команд ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение при команде /start."""
    user = update.effective_user
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! Пока что я бот для заметок. "
        "Чтобы сохранить заметку, просто отправь мне текст. "
        "Для добавления хэштегов используй #хештег. "
        "Для напоминания используй формат 'текст заметки #тег #другой_тег @2025-12-31 10:00'.\n"
        "Команды:\n"
        "/find #хештег - найти заметки по хештегу\n"
        "/all_notes - показать все твои заметки\n"
        "/help - показать это сообщение снова"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет справочное сообщение при команде /help."""
    await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает входящие текстовые сообщения и сохраняет их как заметки."""
    user_id = update.effective_user.id
    message_text = update.message.text

    # Ищем хэштеги в тексте (слова, начинающиеся с #)
    hashtags = re.findall(r'#(\w+)', message_text)
    hashtags_str = ' '.join(hashtags).lower() if hashtags else None

    # Ищем дату и время для напоминания (формат @HH:MM DD-MM-YYYY)
    # Используем скобки для групп захвата: (ЧЧ:ММ) (ДД-ММ-ГГГГ)
    reminder_match = re.search(r'@(\d{2}:\d{2})\s+(\d{2}-\d{2}-\d{4})', message_text)
    reminder_date = None
    if reminder_match:
        time_str = reminder_match.group(1) # Например, "10:00"
        date_str = reminder_match.group(2) # Например, "31-12-2025"
        try:
            # Совмещаем строку даты и времени
            full_datetime_str = f"{date_str} {time_str}"
            reminder_date = datetime.strptime(full_datetime_str, '%d-%m-%Y %H:%M')
        except ValueError:
            await update.message.reply_text("Неверный формат даты/времени для напоминания. Используйте @ЧЧ:ММ ДД-ММ-ГГГГ.")
            return
    else:
        # Если не нашли полный формат, попробуем только дату (ДД-ММ-ГГГГ)
        # Напоминание: сейчас 2025-07-03 19:16:52 PM CEST.
        date_only_match = re.search(r'@(\d{2}-\d{2}-\d{4})', message_text)
        if date_only_match:
            date_str = date_only_match.group(1)
            try:
                # Если указана только дата, устанавливаем время по умолчанию, например, 09:00
                reminder_date = datetime.strptime(date_str, '%d-%m-%Y').replace(hour=9, minute=0)
            except ValueError:
                await update.message.reply_text("Неверный формат даты для напоминания. Используйте @ДД-ММ-ГГГГ или @ЧЧ:ММ ДД-ММ-ГГГГ.")
                return


    # Удаляем хэштеги и метку напоминания из текста заметки для сохранения
    note_text = re.sub(r'#\w+', '', message_text).strip() # Удаляем хэштеги
    if reminder_match:
        note_text = note_text.replace(reminder_match.group(0), '').strip() # Удаляем метку напоминания с временем и датой
    elif date_only_match:
        note_text = note_text.replace(date_only_match.group(0), '').strip() # Удаляем метку напоминания только с датой

    if not note_text:
        await update.message.reply_text("Пожалуйста, введите текст заметки.")
        return

    add_note(user_id, note_text, hashtags_str, reminder_date)
    response_text = "Заметка сохранена!"
    if hashtags_str:
        response_text += f"\nХэштеги: {hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response_text += f"\nНапоминание установлено на: {reminder_date.strftime('%H:%M %d-%m-%Y')}"

    await update.message.reply_text(response_text)

async def find_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ищет заметки по указанному хэштегу."""
    user_id = update.effective_user.id
    # Ожидаем команду в формате /find #хештег
    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите хэштег для поиска. Пример: /find #важно")
        return

    hashtag = context.args[0].lower() # Берем первый аргумент и приводим к нижнему регистру
    if not hashtag.startswith('#'):
        await update.message.reply_text("Хэштег должен начинаться с '#'. Пример: /find #важно")
        return
    
    # Удаляем # из хэштега перед поиском, так как в базе мы храним без него
    search_hashtag = hashtag[1:]

    notes = find_notes_by_user_and_hashtag(user_id, search_hashtag)

    if notes:
        response = f"Найденные заметки по хэштегу '{hashtag}':\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (# {note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%Y-%m-%d %H:%M')})"
            response += "\n"
    else:
        response = f"Заметок по хэштегу '{hashtag}' не найдено."

    await update.message.reply_text(response)

async def all_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает все заметки пользователя."""
    user_id = update.effective_user.id
    notes = get_all_notes_for_user(user_id)

    if notes:
        response = "Все твои заметки:\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (# {note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%Y-%m-%d %H:%M')})"
            response += "\n"
    else:
        response = "У тебя пока нет заметок."

    await update.message.reply_text(response)


# --- Функция для проверки напоминаний ---

async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Проверяет базу данных на наличие напоминаний, которые должны сработать
    в течение следующих 24 часов, и отправляет уведомления.
    """
    logger.info("Проверка напоминаний...")
    reminders = get_upcoming_reminders()

    for note in reminders:
        # Убедимся, что мы отправляем уведомление только за 24 часа до даты,
        # а не прямо в момент наступления.
        # Это упрощенная логика: если напоминание находится в диапазоне (сейчас, сейчас + 24ч)
        # и оно еще не сработало, то отправляем. Для более сложной логики
        # нужно добавить поле "is_notified" в базу данных.
        
        # Здесь мы просто отправляем уведомление, если оно попадает в окно
        # "за сутки". Для предотвращения повторных уведомлений
        # при каждом запуске этой функции (каждые 10 минут)
        # нужно либо добавить флаг в базу данных (рекомендуется!),
        # либо проверять, не было ли уже отправлено уведомление для этого `job`.
        
        # Для простоты примера, мы пока не делаем сложной проверки на повторные уведомления
        # в рамках одного 24-часового окна. Если бот перезапустится, или эта функция
        # сработает повторно, уведомление может быть отправлено еще раз.
        
        # Важно: В реальном проекте, для предотвращения многократных уведомлений,
        # добавьте колонку `is_notified` (BOOLEAN) в таблицу `notes`
        # и обновляйте ее после отправки уведомления.

        # Отправляем уведомление
        try:
            await context.bot.send_message(
                chat_id=note.user_id,
                text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%Y-%m-%d %H:%M')}."
            )
            logger.info(f"Отправлено напоминание пользователю {note.user_id} для заметки {note.id}")
        except Exception as e:
            logger.error(f"Не удалось отправить напоминание пользователю {note.user_id}: {e}")


def main() -> None:
    """Запускает бота и Flask-сервер."""
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise Exception("TELEGRAM_BOT_TOKEN environment variable is not set! Please set it.")

    # Получаем порт из переменной окружения Render.com
    PORT = int(os.environ.get("PORT", 10000)) # <--- Эта строка должна быть здесь

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("find", find_notes_command))
    application.add_handler(CommandHandler("all_notes", all_notes_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=600, first=0)

    # --- ИЗМЕНЕННЫЙ БЛОК: Запуск Flask-сервера в отдельном потоке ---
    def run_flask_server():
        print(f"Starting Flask web server on port {PORT}...")
        web_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False) # use_reloader=False важен для продакшена

    # Запускаем Flask-сервер в отдельном потоке
    flask_thread = threading.Thread(target=run_flask_server)
    flask_thread.start()
    # --- КОНЕЦ ИЗМЕНЕННОГО БЛОКА ---

    # Запускаем Telegram-бота. application.run_polling() сама по себе запустит
    # основной цикл событий и будет управлять им.
    print("Starting Telegram bot...")
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()