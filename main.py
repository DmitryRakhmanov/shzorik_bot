import logging
import os
import re
from datetime import datetime, timedelta

from telegram import Update, __version__ as TG_BOT_VERSION
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from database import (
    add_note,
    find_notes_by_user_and_hashtag,
    get_upcoming_reminders,
    get_all_notes_for_user,
    update_note_reminder_date,
    initialize_db
)

# --- Configuration and Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

logger.info(f"Using python-telegram-bot version: {TG_BOT_VERSION}")

# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /start command."""
    user = update.effective_user
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
        "/help - показать это сообщение снова",
        parse_mode=ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    if not message_text:
        return

    # Ignore bot commands sent to message handler
    if message_text.startswith('/'):
        logger.debug(f"Ignoring command-like message in handle_message: {message_text}")
        return

    # Extract datetime / date markers (same as у вас)
    hashtags_str = None
    reminder_date = None
    reminder_string_found = None

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
        date_only_pattern = r'\s*@(\d{2}-\d{2}-\d{4})'
        date_only_match = re.search(date_only_pattern, message_text, re.DOTALL)
        if date_only_match:
            date_str = date_only_match.group(1)
            try:
                reminder_date = datetime.strptime(date_str, '%d-%m-%Y').replace(hour=9, minute=0)
                reminder_string_found = date_only_match.group(0)
            except ValueError as e:
                logger.error(f"Error parsing date-only format: {e}")
                await message_obj.reply_text("Неверный формат даты для напоминания. Используйте @ДД-ММ-ГГГГ или @ЧЧ:ММ ДД-ММ-ГГГГ.")
                return

    # Remove reminder string and extract hashtags
    cleaned_text = message_text
    if reminder_string_found:
        cleaned_text = re.sub(re.escape(reminder_string_found), '', cleaned_text).strip()

    hashtags = re.findall(r'#(\w+)', cleaned_text)
    hashtags_lower = [h.lower() for h in hashtags]
    hashtags_str = ' '.join(hashtags_lower) if hashtags else None

    note_text = re.sub(r'#\w+', '', cleaned_text).strip()

    if not note_text:
        await message_obj.reply_text("Пожалуйста, введите текст заметки.")
        return

    # === IMPORTANT: only save channel posts if they contain #напоминание ===
    if update.channel_post:
        if 'напоминание' not in hashtags_lower:
            logger.info("Channel post doesn't contain #напоминание — skipping saving.")
            return  # silently ignore channel posts without #напоминание

    # Add note to the database (user_id can be None for channel posts)
    add_note(user_id, note_text, hashtags_str, reminder_date)

    response_text = "Заметка сохранена!"
    if hashtags_str:
        response_text += f"\nХэштеги: #{hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response_text += f"\nНапоминание установлено на: {reminder_date.strftime('%H:%M %d-%m-%Y')}"

    # Reply only if the message came from a private/group (not channel), to avoid double-posting in channel
    if not update.channel_post:
        await message_obj.reply_text(response_text)


async def find_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the /find command to search notes by hashtag."""
    user_id = update.effective_user.id
    message_obj = update.effective_message
    if not context.args:
        await message_obj.reply_text("Пожалуйста, укажите хэштег для поиска. Пример: /find #важно")
        return  # <- исправлено: return внутри блока

    hashtag = context.args[0].lower()
    if not hashtag.startswith('#'):
        await message_obj.reply_text("Хэштег должен начинаться с '#'. Пример: /find #важно")
        return

    search_hashtag = hashtag[1:]  # remove '#'
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

    if len(response) > 4000:
        response = response[:3900] + "\n... (список обрезан, слишком много заметок)"
    await message_obj.reply_text(response)


async def all_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        if len(response) > 4000:
            response = response[:3900] + "\n... (список обрезан, слишком много заметок)"
        await message_obj.reply_text(response)
    else:
        await message_obj.reply_text("У тебя пока нет заметок.")


async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows reminders. If invoked in a private chat, will send to the channel specified by TELEGRAM_CHANNEL_ID."""
    logger.info("Command /upcoming_notes invoked.")
    message_obj = update.effective_message
    notes = get_upcoming_reminders()

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

        # If command was typed in channel, reply there. If typed privately, try to send to configured channel.
        if update.effective_chat and update.effective_chat.type in ("group", "supergroup", "channel"):
            await message_obj.reply_text(response)
        else:
            channel_id_str = os.environ.get("TELEGRAM_CHANNEL_ID")
            if channel_id_str:
                try:
                    channel_id = int(channel_id_str)
                    await context.bot.send_message(chat_id=channel_id, text=response)
                    await message_obj.reply_text("Список предстоящих напоминаний отправлен в канал.")
                except Exception as e:
                    logger.error(f"Failed to send upcoming notes to channel: {e}")
                    await message_obj.reply_text("Не удалось отправить в канал. Проверьте TELEGRAM_CHANNEL_ID и права бота.")
            else:
                await message_obj.reply_text(response)
    else:
        await message_obj.reply_text("На данный момент нет предстоящих напоминаний.")


# --- Reminder Checking Function (job) ---
async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.info("Checking for reminders (job).")
    reminders = get_upcoming_reminders()

    channel_id_str = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not channel_id_str:
        logger.error("TELEGRAM_CHANNEL_ID environment variable is not set. Reminders will not be sent to the channel.")
        return
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        logger.error(f"TELEGRAM_CHANNEL_ID '{channel_id_str}' is not a valid integer.")
        return

    for note in reminders:
        try:
            if note.reminder_date:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}."
                )
                logger.info(f"Reminder sent to channel {channel_id} for note {note.id}")
                update_note_reminder_date(note.id)
                logger.info(f"Reminder date for note {note.id} has been reset.")
        except Exception as e:
            logger.error(f"Failed to send reminder to channel {channel_id} for note {note.id}: {e}")


def main() -> None:
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise Exception("TELEGRAM_BOT_TOKEN environment variable is not set! Please set it.")

    PORT = int(os.environ.get("PORT", 8080))
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
    if not WEBHOOK_URL:
        raise Exception("WEBHOOK_URL environment variable is not set! Please set it to your Render.com public URL + /telegram")

    WEBHOOK_SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET_TOKEN")

    initialize_db()
    logger.info("Database initialized.")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("find", find_notes_command))
    application.add_handler(CommandHandler("all_notes", all_notes_command))
    application.add_handler(CommandHandler("upcoming_notes", upcoming_notes_command))

    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & (filters.UpdateType.MESSAGE | filters.UpdateType.CHANNEL_POST), handle_message)
    )

    job_queue = application.job_queue
    # запуск (если приложение постоянно работает) - каждые 5 минут
    job_queue.run_repeating(check_reminders, interval=300, first=0)

    logger.info(f"Starting webhook server on port {PORT} with URL path /telegram")
    logger.info(f"Setting webhook URL to: {WEBHOOK_URL}")

    webhook_params = {
        "listen": "0.0.0.0",
        "port": PORT,
        "url_path": "telegram",
        "webhook_url": WEBHOOK_URL,
        "health_check_path": "/_health",  # Оставляем health endpoint (можно использовать для "пробуждения")
    }

    if WEBHOOK_SECRET_TOKEN:
        webhook_params["secret_token"] = WEBHOOK_SECRET_TOKEN
    else:
        logger.warning("WEBHOOK_SECRET_TOKEN is not set. Webhook will run without secret token validation.")

    application.run_webhook(**webhook_params)
    logger.info("Webhook server started successfully.")


if __name__ == '__main__':
    main()
