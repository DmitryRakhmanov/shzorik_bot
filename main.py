import logging
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

logger.info(f"Using python-telegram-bot version: {TG_BOT_VERSION}")

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_chat.send_message(
        f"Привет, {user.mention_html()}! Я бот для заметок.\n"
        "Чтобы сохранить заметку, просто отправь текст.\n"
        "Хэштеги через #хештег, напоминания через @ЧЧ:ММ ДД-ММ-ГГГГ.\n"
        "/find #хештег - найти заметки по хэштегу\n"
        "/all_notes - показать все заметки\n"
        "/upcoming_notes - показать предстоящие напоминания",
        parse_mode=ParseMode.HTML,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_obj = update.effective_message
    if not message_obj:
        logger.warning("No message object in update")
        return

    user_id = message_obj.from_user.id if message_obj.from_user else None
    message_text = message_obj.text
    if not message_text or message_text.startswith("/"):
        return

    reminder_date = None
    reminder_string_found = None

    full_datetime_pattern = r"\s*@(\d{2}:\d{2})\s+(\d{2}-\d{2}-\d{4})"
    full_datetime_match = re.search(full_datetime_pattern, message_text)
    if full_datetime_match:
        time_str = full_datetime_match.group(1)
        date_str = full_datetime_match.group(2)
        try:
            reminder_date = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
            reminder_string_found = full_datetime_match.group(0)
        except ValueError:
            await message_obj.reply_text(
                "Неверный формат даты/времени. Используйте @ЧЧ:ММ ДД-ММ-ГГГГ"
            )
            return
    else:
        date_only_pattern = r"\s*@(\d{2}-\d{2}-\d{4})"
        date_only_match = re.search(date_only_pattern, message_text)
        if date_only_match:
            date_str = date_only_match.group(1)
            try:
                reminder_date = datetime.strptime(date_str, "%d-%m-%Y").replace(
                    hour=9, minute=0
                )
                reminder_string_found = date_only_match.group(0)
            except ValueError:
                await message_obj.reply_text(
                    "Неверный формат даты. Используйте @ДД-ММ-ГГГГ"
                )
                return

    cleaned_text = message_text
    if reminder_string_found:
        cleaned_text = re.sub(re.escape(reminder_string_found), "", cleaned_text).strip()

    hashtags = re.findall(r"#(\w+)", cleaned_text)
    hashtags_lower = [h.lower() for h in hashtags]
    hashtags_str = " ".join(hashtags_lower) if hashtags else None
    note_text = re.sub(r"#\w+", "", cleaned_text).strip()

    if not note_text:
        await message_obj.reply_text("Пожалуйста, введите текст заметки.")
        return

    if update.channel_post and "напоминание" not in hashtags_lower:
        return

    add_note(user_id, note_text, hashtags_str, reminder_date)

    response_text = "Заметка сохранена!"
    if hashtags_str:
        response_text += f"\nХэштеги: #{hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response_text += f"\nНапоминание на: {reminder_date.strftime('%H:%M %d-%m-%Y')}"

    if not update.channel_post:
        await message_obj.reply_text(response_text)


async def find_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message_obj = update.effective_message
    if not context.args:
        await message_obj.reply_text("Укажите хэштег. Пример: /find #важно")
        return

    hashtag = context.args[0].lower()
    if not hashtag.startswith("#"):
        await message_obj.reply_text("Хэштег должен начинаться с '#'.")
        return

    search_hashtag = hashtag[1:]
    notes = find_notes_by_user_and_hashtag(user_id, search_hashtag)

    if notes:
        response = f"Заметки по '{hashtag}':\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (Хэштеги: #{note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})"
            response += "\n"
    else:
        response = f"Заметок по '{hashtag}' не найдено."

    if len(response) > 4000:
        response = response[:3900] + "\n... (список обрезан)"
    await message_obj.reply_text(response)


async def all_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message_obj = update.effective_message
    notes = get_all_notes_for_user(user_id)

    if notes:
        response = "Все заметки:\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (Хэштеги: #{note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})"
            response += "\n"
        if len(response) > 4000:
            response = response[:3900] + "\n... (список обрезан)"
        await message_obj.reply_text(response)
    else:
        await message_obj.reply_text("У вас пока нет заметок.")


async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_obj = update.effective_message
    tz_str = os.environ.get("TIMEZONE", "Europe/Moscow")
    now = datetime.now(tz=ZoneInfo(tz_str))
    window_end = now + timedelta(minutes=5)
    notes = get_upcoming_reminders_window(now, window_end)

    if notes:
        response = "📅 Предстоящие напоминания:\n"
        for i, note in enumerate(notes):
            if note.reminder_date:
                response += f"{i+1}. {note.text} (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})"
                if note.hashtags:
                    response += f" (Хэштеги: #{note.hashtags.replace(' ', ', #')})"
                response += "\n"
        await message_obj.reply_text(response)
    else:
        await message_obj.reply_text("Нет предстоящих напоминаний.")


async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    tz_str = os.environ.get("TIMEZONE", "Europe/Moscow")
    now = datetime.now(tz=ZoneInfo(tz_str))
    window_end = now + timedelta(minutes=5)
    reminders = get_upcoming_reminders_window(now, window_end)

    channel_id_str = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not channel_id_str:
        logger.error("TELEGRAM_CHANNEL_ID не задан.")
        return
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        logger.error("TELEGRAM_CHANNEL_ID невалидный.")
        return

    for note in reminders:
        if note.reminder_date:
            try:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}.",
                )
                update_note_reminder_date(note.id)
            except Exception as e:
                logger.error(f"Не удалось отправить напоминание {note.id}: {e}")


def main() -> None:
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise Exception("TELEGRAM_BOT_TOKEN не задан!")

    PORT = int(os.environ.get("PORT", 8080))
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
    if not WEBHOOK_URL:
        raise Exception("WEBHOOK_URL не задан!")

    WEBHOOK_SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET_TOKEN")

    initialize_db()
    logger.info("Database initialized.")

    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("find", find_notes_command))
    application.add_handler(CommandHandler("all_notes", all_notes_command))
    application.add_handler(CommandHandler("upcoming_notes", upcoming_notes_command))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & (filters.UpdateType.MESSAGE | filters.UpdateType.CHANNEL_POST),
            handle_message,
        )
    )

    # Job queue
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=300, first=0)

    logger.info(f"Starting webhook server on port {PORT} with URL path /telegram")
    webhook_params = {
        "listen": "0.0.0.0",
        "port": PORT,
        "url_path": "telegram",
        "webhook_url": WEBHOOK_URL,
    }
    if WEBHOOK_SECRET_TOKEN:
        webhook_params["secret_token"] = WEBHOOK_SECRET_TOKEN
    else:
        logger.warning("WEBHOOK_SECRET_TOKEN не задан, секретный токен не используется.")

    application.run_webhook(**webhook_params)
    logger.info("Webhook server started.")


if __name__ == "__main__":
    main()
