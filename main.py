import os
import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, __version__ as TG_BOT_VERSION
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from database import (
    add_note,
    find_notes_by_user_and_hashtag,
    get_all_notes_for_user,
    update_note_reminder_date,
    get_upcoming_reminders_window,
    get_past_unsent_reminders,
    initialize_db,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.info(f"Using python-telegram-bot version: {TG_BOT_VERSION}")

TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")
tz = ZoneInfo(TIMEZONE)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_chat.send_message(
        f"Привет, {user.mention_html()}! Я бот для заметок.\n"
        "Чтобы сохранить заметку, просто отправь текст с #хештегами.\n"
        "Для напоминания: 'текст заметки #тег @ЧЧ:ММ ДД-ММ-ГГГГ'.\n"
        "Напоминания приходят за день до события.\n"
        "Команды: /find #хештег, /all_notes, /upcoming_notes, /help",
        parse_mode=ParseMode.HTML
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.text:
        return

    text = msg.text
    user_id = msg.from_user.id if msg.from_user else None

    hashtags = re.findall(r"#(\w+)", text)
    hashtags_lower = [h.lower() for h in hashtags]
    hashtags_str = " ".join(hashtags_lower) if hashtags else None

    reminder_date = None
    match = re.search(r"@(\d{2}:\d{2}) (\d{2}-\d{2}-\d{4})", text)
    if match:
        time_str, date_str = match.groups()
        naive_dt = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
        reminder_date = naive_dt.replace(tzinfo=tz)

    cleaned_text = re.sub(r"@\d{2}:\d{2} \d{2}-\d{2}-\d{4}", "", text)
    cleaned_text = re.sub(r"#\w+", "", cleaned_text).strip()
    if not cleaned_text:
        await msg.reply_text("Пожалуйста, введите текст заметки.")
        return

    if update.channel_post and "напоминание" not in hashtags_lower:
        return

    note = add_note(user_id, cleaned_text, hashtags_str, reminder_date)

    response = "✅ Заметка сохранена!"
    if hashtags_str:
        response += f"\nХэштеги: #{hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response += f"\n⏰ Напоминание установлено на: {reminder_date.strftime('%H:%M %d-%m-%Y')}"
        response += "\n🔔 Бот уведомит в канале за день до события."
    await msg.reply_text(response)


async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(tz)
    window_end = now + timedelta(minutes=5)

    reminders = get_upcoming_reminders_window(now, window_end)
    past_unsent = get_past_unsent_reminders()  # обработка пропущенных

    all_reminders = reminders + past_unsent

    channel_id_str = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not channel_id_str:
        logger.error("TELEGRAM_CHANNEL_ID not set!")
        return
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        logger.error("TELEGRAM_CHANNEL_ID must be integer!")
        return

    for note in all_reminders:
        if note.reminder_date:
            send_text = f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}"
            try:
                await context.bot.send_message(chat_id=channel_id, text=send_text)
                logger.info(f"Reminder sent for note {note.id}")
                update_note_reminder_date(note.id, sent=True)
            except Exception as e:
                logger.error(f"Failed to send reminder for note {note.id}: {e}")


async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now(tz)
    notes = get_upcoming_reminders_window(now, now + timedelta(days=1), only_unsent=False)
    if not notes:
        await update.effective_message.reply_text("На данный момент нет предстоящих напоминаний.")
        return

    resp = "📅 Предстоящие напоминания:\n"
    for i, note in enumerate(notes):
        if note.reminder_date:
            resp += f"{i+1}. {note.text} — {note.reminder_date.strftime('%H:%M %d-%m-%Y')}"
            if note.hashtags:
                resp += f" (Хэштеги: #{note.hashtags.replace(' ', ', #')})"
            resp += "\n"
    await update.effective_message.reply_text(resp)


def main():
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise Exception("Set TELEGRAM_BOT_TOKEN!")

    PORT = int(os.environ.get("PORT", 8080))
    WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
    if not WEBHOOK_URL:
        raise Exception("Set WEBHOOK_URL!")

    initialize_db()
    logger.info("Database initialized.")

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("upcoming_notes", upcoming_notes_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Проверка напоминаний каждые 5 минут
    application.job_queue.run_repeating(send_reminders, interval=300, first=0)

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path="telegram",
        webhook_url=WEBHOOK_URL
    )


if __name__ == "__main__":
    main()
