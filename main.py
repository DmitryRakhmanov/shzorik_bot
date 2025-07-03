import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
import re
from datetime import datetime, timedelta
from database import add_note, find_notes_by_user_and_hashtag, get_upcoming_reminders, get_all_notes_for_user
import asyncio
import os
from flask import Flask, request
import threading

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

# --- Обработчики команд (без изменений) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message_text = update.message.text

    hashtags = re.findall(r'#(\w+)', message_text)
    hashtags_str = ' '.join(hashtags).lower() if hashtags else None

    reminder_match = re.search(r'@(\d{2}:\d{2})\s+(\d{2}-\d{2}-\d{4})', message_text)
    reminder_date = None
    if reminder_match:
        time_str = reminder_match.group(1)
        date_str = reminder_match.group(2)
        try:
            full_datetime_str = f"{date_str} {time_str}"
            reminder_date = datetime.strptime(full_datetime_str, '%d-%m-%Y %H:%M')
        except ValueError:
            await update.message.reply_text("Неверный формат даты/времени для напоминания. Используйте @ЧЧ:ММ ДД-ММ-ГГГГ.")
            return
    else:
        date_only_match = re.search(r'@(\d{2}-\d{2}-\d{4})', message_text)
        if date_only_match:
            date_str = date_only_match.group(1)
            try:
                reminder_date = datetime.strptime(date_str, '%d-%m-%Y').replace(hour=9, minute=0)
            except ValueError:
                await update.message.reply_text("Неверный формат даты для напоминания. Используйте @ДД-ММ-ГГГГ или @ЧЧ:ММ ДД-ММ-ГГГГ.")
                return

    note_text = re.sub(r'#\w+', '', message_text).strip()
    if reminder_match:
        note_text = note_text.replace(reminder_match.group(0), '').strip()
    elif date_only_match:
        note_text = note_text.replace(date_only_match.group(0), '').strip()

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
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите хэштег для поиска. Пример: /find #важно")
        return

    hashtag = context.args[0].lower()
    if not hashtag.startswith('#'):
        await update.message.reply_text("Хэштег должен начинаться с '#'. Пример: /find #важно")
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
                response += f" (Напоминание: {note.reminder_date.strftime('%Y-%m-%d %H:%M')})"
            response += "\n"
    else:
        response = f"Заметок по хэштегу '{hashtag}' не найдено."

    await update.message.reply_text(response)

async def all_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        await update.message.reply_text(response)
    else:
        await update.message.reply_text("У тебя пока нет заметок.")

# --- Функция для проверки напоминаний ---
async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Проверка напоминаний...")
    reminders = get_upcoming_reminders()

    for note in reminders:
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

    PORT = int(os.environ.get("PORT", 10000))

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("find", find_notes_command))
    application.add_handler(CommandHandler("all_notes", all_notes_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=600, first=0)

    def run_flask_server():
        print(f"Starting Flask web server on port {PORT}...")
        web_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

    flask_thread = threading.Thread(target=run_flask_server)
    flask_thread.start()

    print("Starting Telegram bot...")
    # =========================================================================
    # ВРЕМЕННЫЙ КОД ДЛЯ ОЧИСТКИ СОСТОЯНИЯ TELEGRAM API
    # Этот блок должен быть удален ПОСЛЕ успешного развертывания
    # и исчезновения ошибки Conflict.
    # =========================================================================
    async def one_time_cleanup():
        # Получаем текущий цикл событий, если он уже запущен
        loop = asyncio.get_event_loop()
        if not loop.is_running():
            # Если цикл не запущен, то мы не можем вызвать await
            # В этом случае, возможно, вам придётся запустить его вручную
            # или использовать application.run_polling(clean=True) без этого
            logger.warning("Asyncio event loop is not running. Cannot perform webhook cleanup.")
            return

        try:
            # Попытка удалить любой существующий вебхук.
            # Это часто сбрасывает состояние polling на сервере Telegram.
            await application.bot.delete_webhook()
            logger.info("Successfully deleted any lingering webhooks.")
        except Exception as e:
            logger.error(f"Failed to delete webhook: {e}")

        # Также, убедимся, что все ожидающие обновления удалены
        # (это уже делает drop_pending_updates=True, но явная очистка никогда не помешает)
        # await application.updater.start_polling(drop_pending_updates=True, clean=True)

    # Запускаем эту одноразовую очистку асинхронно
    # Это должно произойти перед тем, как application.run_polling() возьмет управление циклом
    # Мы НЕ МОЖЕМ использовать asyncio.run() здесь, потому что application.run_polling()
    # создаст свой собственный цикл.
    # Лучший подход - использовать `clean=True` в `run_polling` как мы уже делали.
    # Если `clean=True` не помогает, то, вероятно, проблема в Render, который запускает
    # несколько процессов или не завершает старые.
    #
    # Давайте попробуем ещё одну вещь, если clean=True не сработало:
    # Убедимся, что application.run_polling() - это последнее, что запускается в потоке.
    # И попробуем сделать "Deploy Clear Cache & Deploy" несколько раз.
    # Если это не поможет, то, возможно, стоит сделать "hard reset" на стороне Telegram
    # (но это сложнее и требует другого подхода, обычно не нужно).

    # Вернемся к самому надежному способу без усложнений:
    # application.run_polling сам позаботится о запуске цикла и очистке.
    # Если Conflict persist, то это проблема с жизненным циклом процесса на Render.
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()