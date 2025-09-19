# main.py
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
    get_all_notes_for_user,
    get_upcoming_reminders_window,
    update_note_reminder_date,
    initialize_db,
)

# --- Logging ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.info(f"Using python-telegram-bot version: {TG_BOT_VERSION}")

# --- Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_chat.send_message(
        f"Привет, {user.mention_html()}! Я бот для заметок.\n"
        "Формат для напоминаний: 'текст заметки #тег @ЧЧ:ММ ДД-ММ-ГГГГ'.\n"
        "Напоминания приходят в канал за 24 часа до события.\n"
        "Команды:\n"
        "/find #хештег - найти заметки по хештегу\n"
        "/all_notes - показать все твои заметки\n"
        "/upcoming_notes - показать все предстоящие напоминания\n"
        "/help - показать это сообщение снова",
        parse_mode=ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)

# --- Handle incoming messages ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_obj = update.effective_message
    if not message_obj:
        logger.warning("Received update without effective_message.")
        return

    user_id = message_obj.from_user.id if message_obj.from_user else None
    message_text = message_obj.text
    if not message_text:
        return

    if message_text.startswith('/'):
        return  # ignore commands

    # --- Extract reminder ---
    reminder_date = None
    reminder_found = None

    full_pattern = r'@(\d{2}:\d{2})\s+(\d{2}-\d{2}-\d{4})'
    m = re.search(full_pattern, message_text)
    if m:
        time_str, date_str = m.groups()
        try:
            reminder_date = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
            reminder_date = reminder_date.replace(tzinfo=ZoneInfo("Europe/Moscow"))
            reminder_found = m.group(0)
        except ValueError:
            await message_obj.reply_text("Неверный формат даты/времени для напоминания.")
            return
    else:
        date_only_pattern = r'@(\d{2}-\d{2}-\d{4})'
        m2 = re.search(date_only_pattern, message_text)
        if m2:
            date_str = m2.group(1)
            try:
                reminder_date = datetime.strptime(date_str, "%d-%m-%Y").replace(
                    hour=9, minute=0, tzinfo=ZoneInfo("Europe/Moscow")
                )
                reminder_found = m2.group(0)
            except ValueError:
                await message_obj.reply_text("Неверный формат даты для напоминания.")
                return

    # --- Extract hashtags ---
    cleaned_text = message_text
    if reminder_found:
        cleaned_text = re.sub(re.escape(reminder_found), "", cleaned_text).strip()
    hashtags = re.findall(r'#(\w+)', cleaned_text)
    hashtags_lower = [h.lower() for h in hashtags]
    hashtags_str = " ".join(hashtags_lower) if hashtags else None
    note_text = re.sub(r'#\w+', '', cleaned_text).strip()
    if not note_text:
        await message_obj.reply_text("Пожалуйста, введите текст заметки.")
        return

    # Only save channel posts if #напоминание
    if update.channel_post and "напоминание" not in hashtags_lower:
        return

    add_note(user_id, note_text, hashtags_str, reminder_date)

    response = "Заметка сохранена!"
    if hashtags_str:
        response += f"\nХэштеги: #{hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response += f"\nНапоминание установлено на: {reminder_date.strftime('%H:%M %d-%m-%Y')}"
    if not update.channel_post:
        await message_obj.reply_text(response)

# --- Find notes ---
async def find_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Укажите хэштег. Пример: /find #важно")
        return
    hashtag = context.args[0].lower().lstrip("#")
    user_id = update.effective_user.id
    notes = find_notes_by_user_and_hashtag(user_id, hashtag)
    if notes:
        response = f"Заметки по #{hashtag}:\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (Хэштеги: #{note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})"
            response += "\n"
    else:
        response = f"Заметок по #{hashtag} не найдено."
    await update.message.reply_text(response)

# --- All notes ---
async def all_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
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
    else:
        response = "Нет заметок."
    await update.message.reply_text(response)

# --- Upcoming reminders ---
async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tz_str = "Europe/Moscow"
    now = datetime.now(tz=ZoneInfo(tz_str))
    in_24h = now + timedelta(hours=24)
    notes = get_upcoming_reminders_window(now, in_24h)
    if notes:
        response = "📅 Предстоящие напоминания:\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text} (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})\n"
        if update.effective_chat.type in ("group", "supergroup", "channel"):
            await update.message.reply_text(response)
        else:
            channel_id = int(os.environ.get("TELEGRAM_CHANNEL_ID"))
            await context.bot.send_message(channel_id, response)
            await update.message.reply_text("Список предстоящих напоминаний отправлен в канал.")
    else:
        await update.message.reply_text("Нет предстоящих напоминаний.")

# --- Reminder job ---
async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    tz_str = "Europe/Moscow"
    now = datetime.now(tz=ZoneInfo(tz_str))
    in_5_min = now + timedelta(minutes=5)
    reminders = get_upcoming_reminders_window(now, in_5_min)
    channel_id = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not channel_id:
        logger.error("TELEGRAM_CHANNEL_ID не установлен")
        return
    channel_id = int(channel_id)
    for note in reminders:
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}."
            )
            update_note_reminder_date(note.id)
        except Exception as e:
            logger.error(f"Не удалось отправить напоминание для note_id={note.id}: {e}")

# --- Main ---
def main() -> None:
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise Exception("TELEGRAM_BOT_TOKEN не установлен")
    PORT = int(os.environ.get("PORT", 8080))
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
    if not WEBHOOK_URL:
        raise Exception("WEBHOOK_URL не установлен")
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

    # Job queue
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=300, first=0)

    # Webhook
    webhook_params = {
        "listen": "0.0.0.0",
        "port": PORT,
        "url_path": "telegram",
        "webhook_url": WEBHOOK_URL,
        "health_check_path": "/_health",
    }
    if WEBHOOK_SECRET_TOKEN:
        webhook_params["secret_token"] = WEBHOOK_SECRET_TOKEN
    else:
        logger.warning("WEBHOOK_SECRET_TOKEN не установлен. Webhook без валидации секретного токена.")

    application.run_webhook(**webhook_params)
    logger.info("Webhook server started successfully.")

if __name__ == "__main__":
    main()
