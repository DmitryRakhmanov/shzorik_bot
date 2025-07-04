import logging
import os
import re
from datetime import datetime, timedelta

# Import necessary Telegram modules
from telegram import Update, __version__ as TG_BOT_VERSION # <--- ДОБАВЛЕНО: Импорт для проверки версии
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    JobQueue,
)

# Import database functions (ensure your database.py is correctly set up)
from database import (
    add_note,
    find_notes_by_user_and_hashtag,
    get_upcoming_reminders,
    get_all_notes_for_user,
    update_note_reminder_date,
    initialize_db # Ensure this function exists in database.py
)

# --- Configuration and Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# <--- ДОБАВЛЕНО: Логирование версии python-telegram-bot
logger.info(f"Using python-telegram-bot version: {TG_BOT_VERSION}")

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    user = update.effective_user
    # Use effective_chat.id for replies to ensure it goes to the correct chat
    await update.effective_chat.send_message(
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
    """Handles the /help command."""
    await start(update, context)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles incoming text messages to create notes."""
    message_obj = update.effective_message
    if not message_obj:
        logger.warning("Received update without effective_message.")
        return

    user_id = message_obj.from_user.id if message_obj.from_user else None
    
    # Log where the message came from (private/group or channel)
    if update.message:
        logger.info(f"Message received from private/group chat. User ID: {user_id}, Chat ID: {message_obj.chat_id}")
    elif update.channel_post:
        logger.info(f"Message received from channel. Channel ID: {message_obj.chat_id}")

    message_text = message_obj.text
    logger.info(f"Received message from user {user_id} (if available) / from chat {message_obj.chat_id}: '{message_text}'")

    # If the message starts with '/', it's likely a command that wasn't caught by CommandHandler
    # or was sent to the bot directly without the bot's username in a group/channel.
    # We log it and ignore it here to prevent accidental note creation from commands.
    if message_text and message_text.startswith('/'):
        logger.warning(f"MessageHandler received command: '{message_text}'. Ignoring in handle_message.")
        return 

    hashtags_str = None
    reminder_date = None
    reminder_string_found = None

    # Regex for full datetime format: @HH:MM DD-MM-YYYY
    full_datetime_pattern = r'\s*@(\d{2}:\d{2})\s+(\d{2}-\d{2}-\d{4})'
    full_datetime_match = re.search(full_datetime_pattern, message_text, re.DOTALL)

    if full_datetime_match:
        time_str = full_datetime_match.group(1)
        date_str = full_datetime_match.group(2)
        try:
            reminder_date = datetime.strptime(f"{date_str} {time_str}", '%d-%m-%Y %H:%M')
            reminder_string_found = full_datetime_match.group(0)
        except ValueError as e:
            logger.error(f"Error parsing full datetime format: {e}")
            await message_obj.reply_text("Неверный формат даты/времени для напоминания. Используйте @ЧЧ:ММ ДД-ММ-ГГГГ.")
            return
    else:
        # Regex for date-only format: @DD-MM-YYYY
        date_only_pattern = r'\s*@(\d{2}-\d{2}-\d{4})'
        date_only_match = re.search(date_only_pattern, message_text, re.DOTALL)
        if date_only_match:
            date_str = date_only_match.group(1)
            try:
                # Default to 9 AM if only date is provided
                reminder_date = datetime.strptime(date_str, '%d-%m-%Y').replace(hour=9, minute=0)
                reminder_string_found = date_only_match.group(0)
            except ValueError as e:
                logger.error(f"Error parsing date-only format: {e}")
                await message_obj.reply_text("Неверный формат даты для напоминания. Используйте @ДД-ММ-ГГГГ или @ЧЧ:ММ ДД-ММ-ГГГГ.")
                return

    # Remove the reminder string from the message text
    cleaned_text = message_text
    if reminder_string_found:
        cleaned_text = re.sub(re.escape(reminder_string_found), '', cleaned_text).strip()
    
    # Extract hashtags
    hashtags = re.findall(r'#(\w+)', cleaned_text)
    hashtags_str = ' '.join(hashtags).lower() if hashtags else None

    # Remove hashtags from the final note text
    note_text = re.sub(r'#\w+', '', cleaned_text).strip()

    if not note_text:
        await message_obj.reply_text("Пожалуйста, введите текст заметки.")
        return

    # Add note to the database
    add_note(user_id, note_text, hashtags_str, reminder_date)
    response_text = "Заметка сохранена!"
    if hashtags_str:
        response_text += f"\nХэштеги: #{hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response_text += f"\nНапоминание установлено на: {reminder_date.strftime('%H:%M %d-%m-%Y')}"

    await message_obj.reply_text(response_text)


async def find_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /find command to search notes by hashtag."""
    user_id = update.effective_user.id
    message_obj = update.effective_message
    if not context.args:
        await message_obj.reply_text("Пожалуйста, укажите хэштег для поиска. Пример: /find #важно")
    return

    hashtag = context.args[0].lower()
    if not hashtag.startswith('#'):
        await message_obj.reply_text("Хэштег должен начинаться с '#'. Пример: /find #важно")
        return
    
    search_hashtag = hashtag[1:] # Remove '#' for database search

    notes = find_notes_by_user_and_hashtag(user_id, search_hashtag)

    if notes:
        response = f"Найденные заметки по хэштегу '{hashtag}':\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (Хэштеги: #{note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})"
            response += "\n"
    else:
        response = f"Заметок по хэштегу '{hashtag}' не найдено."

    # Truncate long responses
    if len(response) > 4000:
         response = response[:3900] + "\n... (список обрезан, слишком много заметок)"
    await message_obj.reply_text(response)

async def all_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /all_notes command to show all notes for a user."""
    user_id = update.effective_user.id
    message_obj = update.effective_message
    notes = get_all_notes_for_user(user_id)

    if notes:
        response = "Все твои заметки:\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (Хэштеги: #{note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})"
            response += "\n"
        # Truncate long responses
        if len(response) > 4000:
             response = response[:3900] + "\n... (список обрезан, слишком много заметок)"
        await message_obj.reply_text(response)
    else:
        await message_obj.reply_text("У тебя пока нет заметок.")

async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /upcoming_notes command to show reminders in the channel."""
    logger.info("Command /upcoming_notes invoked.")
    message_obj = update.effective_message 
    notes = get_upcoming_reminders() # This fetches all reminders, regardless of user_id

    if notes:
        response = "📅 Предстоящие напоминания:\n"
        for i, note in enumerate(notes):
            if note.reminder_date:
                formatted_date = note.reminder_date.strftime('%H:%M %d-%m-%Y')
                response += f"{i+1}. {note.text} (Напоминание: {formatted_date})"
                if note.hashtags:
                    response += f" (Хэштеги: #{note.hashtags.replace(' ', ', #')})"
                response += "\n"
        if len(response) > 4000:
             response = response[:3900] + "\n... (список обрезан, слишком много заметок)"
        await message_obj.reply_text(response)
    else:
        response = "На данный момент нет предстоящих напоминаний."
        await message_obj.reply_text(response)

# --- Reminder Checking Function (for APScheduler) ---

async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job to check for and send upcoming reminders."""
    logger.info("Checking for reminders...")
    reminders = get_upcoming_reminders() # Get all reminders from DB
    
    channel_id_str = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not channel_id_str:
        logger.error("TELEGRAM_CHANNEL_ID environment variable is not set. Reminders will not be sent to the channel.")
        return
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        logger.error(f"TELEGRAM_CHANNEL_ID '{channel_id_str}' is not a valid integer. Reminders will not be sent.")
        return

    for note in reminders:
        try:
            # Send reminder to the channel
            if note.reminder_date:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}."
                )
                logger.info(f"Reminder sent to channel {channel_id} for note {note.id}")
                
                # After sending, reset reminder_date in DB to prevent resending
                update_note_reminder_date(note.id)
                logger.info(f"Reminder date for note {note.id} has been reset.")

        except Exception as e:
            logger.error(f"Failed to send reminder to channel {channel_id} for note {note.id}: {e}")

# --- Main Bot Function ---

def main() -> None:
    """Starts the bot using webhooks."""
    # Retrieve environment variables
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise Exception("TELEGRAM_BOT_TOKEN environment variable is not set! Please set it.")

    PORT = int(os.environ.get("PORT", 8080)) # Default to 8080 or 443 for webhooks
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
    if not WEBHOOK_URL:
        raise Exception("WEBHOOK_URL environment variable is not set! Please set it to your Render.com public URL + /telegram")
    
    WEBHOOK_SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET_TOKEN")
    # For initial debugging, it's often easier to disable secret_token check
    # if you're not setting it via BotFather API (which BotFather UI doesn't allow).
    # If you remove it here, remember to re-enable and configure properly for production.
    # if not WEBHOOK_SECRET_TOKEN:
    #     logger.warning("WEBHOOK_SECRET_TOKEN environment variable is not set. It is highly recommended for webhook security.")
    #     WEBHOOK_SECRET_TOKEN = "your_strong_secret_token_here_change_me" # Fallback, but set a real one!

    # Initialize the database (creates tables if they don't exist)
    initialize_db() 
    logger.info("Database initialized.")

    # Create the Application and pass it your bot's token.
    application = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("find", find_notes_command))
    application.add_handler(CommandHandler("all_notes", all_notes_command))
    application.add_handler(CommandHandler("upcoming_notes", upcoming_notes_command))
    
    # Message handler for general text messages, excluding commands, from messages AND channel posts
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.UpdateType.MESSAGE | filters.UpdateType.CHANNEL_POST), handle_message)
    )

    # Add a job queue to run scheduled tasks (like checking reminders)
    job_queue = application.job_queue
    # Run check_reminders every 5 minutes (300 seconds)
    job_queue.run_repeating(check_reminders, interval=300, first=0) 

    # --- Start the Bot via Webhook ---
    logger.info(f"Starting webhook server on port {PORT} with URL path /telegram")
    logger.info(f"Setting webhook URL to: {WEBHOOK_URL}")

    webhook_params = {
        "listen": "0.0.0.0", # Listen on all available network interfaces
        "port": PORT,
        "url_path": "telegram", # The specific path for the webhook (e.g., https://your-service.onrender.com/telegram)
        "webhook_url": WEBHOOK_URL,
        "health_check_path": "/_health",  # <--- ДОБАВЛЕНО: Для проверки работоспособности Render
    }

    # Only add secret_token if it's explicitly set (better for production)
    # Если вы устанавливаете вебхук через BotFather, то Telegram НЕ отправляет secret_token.
    # В этом случае либо не передавайте secret_token в run_webhook, либо используйте API Telegram
    # для установки вебхука с secret_token.
    if WEBHOOK_SECRET_TOKEN:
        webhook_params["secret_token"] = WEBHOOK_SECRET_TOKEN
    else:
        logger.warning("WEBHOOK_SECRET_TOKEN is not set. Webhook will run without secret token validation.")

    application.run_webhook(**webhook_params)
    logger.info("Webhook server started successfully.")

if __name__ == '__main__':
    main()