import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, JobQueue
import re
from datetime import datetime, timedelta
from database import add_note, find_notes_by_user_and_hashtag, get_upcoming_reminders, get_all_notes_for_user
import asyncio # Although not directly used for async cleanup, it's a common import
import os
from flask import Flask, request
import threading

# Create a Flask app for health checks (required by Render.com)
web_app = Flask(__name__)

# Define a health check route
@web_app.route('/health')
def health_check():
    """Endpoint for Render.com to check if the service is running."""
    return 'OK', 200 # Returns "OK" with a 200 status code

# Configure logging to see what's happening
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message and instructions on /start command."""
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
    """Sends the help message (same as start)."""
    await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes incoming text messages to save them as notes, extracting hashtags and reminders."""
    user_id = update.effective_user.id
    message_text = update.message.text

    # Extract hashtags
    hashtags = re.findall(r'#(\w+)', message_text)
    hashtags_str = ' '.join(hashtags).lower() if hashtags else None

    # Extract reminder date and time
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
        # Check for date-only reminder (defaults to 9 AM)
        date_only_match = re.search(r'@(\d{2}-\d{2}-\d{4})', message_text)
        if date_only_match:
            date_str = date_only_match.group(1)
            try:
                reminder_date = datetime.strptime(date_str, '%d-%m-%Y').replace(hour=9, minute=0)
            except ValueError:
                await update.message.reply_text("Неверный формат даты для напоминания. Используйте @ДД-ММ-ГГГГ или @ЧЧ:ММ ДД-ММ-ГГГГ.")
                return

    # Clean the note text by removing hashtags and reminder parts
    note_text = re.sub(r'#\w+', '', message_text).strip()
    if reminder_match:
        note_text = note_text.replace(reminder_match.group(0), '').strip()
    elif date_only_match:
        note_text = note_text.replace(date_only_match.group(0), '').strip()

    if not note_text:
        await update.message.reply_text("Пожалуйста, введите текст заметки.")
        return

    # Add the note to the database
    add_note(user_id, note_text, hashtags_str, reminder_date)
    response_text = "Заметка сохранена!"
    if hashtags_str:
        response_text += f"\nХэштеги: {hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response_text += f"\nНапоминание установлено на: {reminder_date.strftime('%H:%M %d-%m-%Y')}"

    await update.message.reply_text(response_text)

async def find_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Finds and displays notes based on a specified hashtag."""
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите хэштег для поиска. Пример: /find #важно")
        return

    hashtag = context.args[0].lower()
    if not hashtag.startswith('#'):
        await update.message.reply_text("Хэштег должен начинаться с '#'. Пример: /find #важно")
        return
    
    search_hashtag = hashtag[1:] # Remove '#' for database search

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

    await update.message.reply_text(response)

async def all_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays all notes stored for the current user."""
    user_id = update.effective_user.id
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
        await update.message.reply_text(response)
    else:
        await update.message.reply_text("У тебя пока нет заметок.")


# --- Reminder Checking Function ---

async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Checks the database for upcoming reminders (next 24 hours)
    and sends them to the specified Telegram channel.
    """
    logger.info("Проверка напоминаний...")
    reminders = get_upcoming_reminders()
    
    # Get the channel ID from environment variables.
    # This ID must be set on Render.com for reminders to be sent.
    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not channel_id:
        logger.error("TELEGRAM_CHANNEL_ID is not set in environment variables. Reminders will not be sent to the channel.")
        return # Exit if channel ID is not set

    for note in reminders:
        try:
            # Send the reminder message to the specified channel
            await context.bot.send_message(
                chat_id=channel_id, # Reminders are sent to the channel
                text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}."
            )
            logger.info(f"Отправлено напоминание в канал {channel_id} для заметки {note.id}")
        except Exception as e:
            logger.error(f"Не удалось отправить напоминание в канал {channel_id}: {e}")


def main() -> None:
    """
    Main function to start the Telegram bot and Flask web server.
    Ensures environment variables are set and configures bot handlers.
    """
    # Get Telegram bot token from environment variable
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise Exception("TELEGRAM_BOT_TOKEN environment variable is not set! Please set it.")

    # Get the port for Flask server from environment variable (default to 10000 for Render)
    PORT = int(os.environ.get("PORT", 10000))

    # Build the Telegram bot Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("find", find_notes_command))
    application.add_handler(CommandHandler("all_notes", all_notes_command))
    
    # Add a message handler for all text messages that are not commands
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Set up JobQueue for recurring tasks (like checking reminders)
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=600, first=0) # Check every 600 seconds (10 minutes)

    # Function to run the Flask web server in a separate thread
    def run_flask_server():
        print(f"Starting Flask web server on port {PORT}...")
        # debug=False and use_reloader=False are important for production environments
        web_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

    # Start the Flask server in a daemon thread so it doesn't block the main bot thread
    flask_thread = threading.Thread(target=run_flask_server)
    flask_thread.daemon = True # Allows the main program to exit even if this thread is still running
    flask_thread.start()

    print("Starting Telegram bot...")
    # Start the bot using long polling.
    # drop_pending_updates=True ensures that any messages received while the bot was offline
    # or during a previous conflicting session are ignored upon startup.
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()