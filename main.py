import logging
import os
import re
from datetime import datetime, timedelta, timezone

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
    get_upcoming_reminders_window,
    get_all_notes_for_user,
    update_note_reminder_date,
    initialize_db,
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

    if update.message:
        logger.info(f"Message from chat. User ID: {user_id}, Chat ID: {message_obj.chat_id}")
    elif update.channel_post:
        logger.info(f"Message from channel. Channel ID: {message_obj.chat_id}")

    message_text = message_obj.text
    if not message_text:
        return

    if message_text.startswith('/'):
        return  # игнорируем команды здесь

    hashtags_str = None
    reminder_date = None
    reminder_string_found = None

    # Полный формат: @ЧЧ:ММ ДД-ММ-ГГГГ
    full_datetime_pattern = r'\s*@(\d{2}:\d{2})\s+(\d{2}-\d{2}-\d{4})'
    full_datetime_match = re.search(full_datetime_pattern, message_text, re.DOTALL)

    if full_datetime_match:
        time_str = full_datetime_match.group(1)
        date_str = full_datetime_match.group(2)
        try:
            reminder_date = datetime.strptime(f"{date_str} {time_str}", '%d-%m-%Y %H:%M').replace(tzinfo=timezone.utc)
            reminder_string_found = full_datetime_match.group(0)
        except ValueError:
            await message_obj.reply_text("Неверный формат даты/времени. Используйте @ЧЧ:ММ ДД-ММ-ГГГГ.")
            return
    else:
        # Только дата: @ДД-ММ-ГГГГ (по умолчанию 09:00)
        date_only_pattern = r'\s*@(\d{2}-\d{2}-\d{4})'
        date_only_match = re.search(date_only_pattern, message_text, re.DOTALL)
        if date_only_match:
            date_str = date_only_match.group(1)
            try:
                reminder_date = datetime.strptime(date_str, '%d-%m-%Y').replace(hour=9, minute=0, tzinfo=timezone.utc)
                reminder_string_found = date_only_match.group(0)
            except ValueError:
                await message_obj.reply_text("Неверный формат даты. Используйте @ДД-ММ-ГГГГ или @ЧЧ:ММ ДД-ММ-ГГГГ.")
                return

    # Очистка текста
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

    if update.channel_post and 'напоминание' not in hashtags_lower:
        return  # игнорируем посты в канале без #напоминание

    add_note(user_id, note_text, hashtags_str, reminder_date)

    response_text = "Заметка сохранена!"
    if hashtags_str:
        response_text += f"\nХэштеги: #{hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response_text += f"\nНапоминание: {reminder_date.strftime('%H:%M %d-%m-%Y')}"

    if not update.channel_post:
        await message_obj.reply_text(response_text)


async def find_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message_obj = update.effective_message
    if not context.args:
        await message_obj.reply_text("Пожалуйста, укажите хэштег. Пример: /find #важно")
        return

    hashtag = context.args[0].lower()
    if not hashtag.startswith('#'):
        await message_obj.reply_text("Хэштег должен начинаться с '#'.")
        return

    search_hashtag = hashtag[1:]
    notes = find_notes_by_user_and_hashtag(user_id, search_hashtag)

    if notes:
        response = f"Найденные заметки по {hashtag}:\n"
        for i, note in enumerate(notes, start=1):
            response += f"{i}. {note.text}\n"
    else:
        response = f"Заметок по {hashtag} не найдено."

    await message_obj.reply_text(response[:4000])


async def all_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message_obj = update.effective_message
    notes = get_all_notes_for_user(user_id)

    if notes:
        response = "Все твои заметки:\n"
        for i, note in enumerate(notes, start=1):
            response += f"{i}. {note.text}\n"
    else:
        response = "У тебя пока нет заметок."

    await message_obj.reply_text(response[:4000])


async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(timezone.utc)
    notes = get_upcoming_reminders_window(now, now + timedelta(hours=24))

    if notes:
        response = "📅 Предстоящие напоминания:\n"
        for i, note in enumerate(notes, start=1):
            if note.reminder_date:
                response += f"{i}. {note.text} (⏰ {note.reminder_date.strftime('%H:%M %d-%m-%Y')})\n"
    else:
        response = "Нет предстоящих напоминаний."

    await update.effective_message.reply_text(response[:4000])


# --- Reminder Checking Function (job) ---
async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(timezone.utc)
    reminders = get_upcoming_reminders_window(now, now + timedelta(minutes=5))

    channel_id_str = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not channel_id_str:
        logger.error("TELEGRAM_CHANNEL_ID is not set.")
        return
    channel_id = int(channel_id_str)

    for note in reminders:
        if note.reminder_date:
            await context.bot.send_message(
                chat_id=channel_id,
                text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}."
            )
            update_note_reminder_date(note.id)


def main() -> None:
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise Exception("TELEGRAM_BOT_TOKEN is not set!")

    PORT = int(os.environ.get("PORT", 8080))
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
    if not WEBHOOK_URL:
        raise Exception("WEBHOOK_URL is not set!")

    WEBHOOK_SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET_TOKEN")

    initialize_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("find", find_notes_command))
    application.add_handler(CommandHandler("all_notes", all_notes_command))
    application.add_handler(CommandHandler("upcoming_notes", upcoming_notes_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=300, first=0)

    webhook_params = {
        "listen": "0.0.0.0",
        "port": PORT,
        "url_path": "telegram",
        "webhook_url": WEBHOOK_URL,
    }
    if WEBHOOK_SECRET_TOKEN:
        webhook_params["secret_token"] = WEBHOOK_SECRET_TOKEN

    application.run_webhook(**webhook_params)


if __name__ == '__main__':
    main()
