import os
import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from database import init_db, add_note, get_upcoming_reminders_window, mark_reminder_sent
from aiohttp import web

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
TELEGRAM_CHANNEL_ID = int(os.environ.get("TELEGRAM_CHANNEL_ID", 0))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
WEBHOOK_PORT = int(os.environ.get("PORT", 10000))
USE_WEBHOOK = os.environ.get("USE_WEBHOOK", "false").lower() in ("true", "1", "t")

if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не задан")

if USE_WEBHOOK and not all([WEBHOOK_URL, WEBHOOK_SECRET, WEBHOOK_PORT]):
    raise ValueError("При USE_WEBHOOK=true нужны WEBHOOK_URL, WEBHOOK_SECRET_TOKEN и PORT")

init_db()
logger.info("Database initialized.")

def parse_reminder(text: str):
    hashtags = re.findall(r"#[а-яА-ЯёЁa-zA-Z0-9_]+", text)
    dt_match = re.search(r"@(\d{2}:\d{2}) (\d{2}-\d{2}-\d{4})", text)
    reminder_date = None
    if dt_match:
        time_str, date_str = dt_match.groups()
        try:
            reminder_date = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
            reminder_date = reminder_date.replace(tzinfo=ZoneInfo("Europe/Moscow"))
        except ValueError:
            return text, " ".join(hashtags), None
    return text, hashtags, reminder_date

async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    cleaned_text, hashtags, reminder_date = parse_reminder(text)
    if "#напоминание" not in hashtags or reminder_date is None:
        await update.message.reply_text("❌ Нужно #напоминание и формат @HH:MM DD-MM-YYYY.")
        return
    note = add_note(user_id, cleaned_text, " ".join(hashtags), reminder_date)
    reply = f"✅ Напоминание сохранено: '{note.text}' на {note.reminder_date.astimezone(ZoneInfo('Europe/Moscow')).strftime('%H:%M %d-%m-%Y')}"
    await update.message.reply_text(reply)
    logger.info(f"Saved reminder: {note.text}")

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    text = update.channel_post.text
    channel_id = update.channel_post.chat.id
    cleaned_text, hashtags, reminder_date = parse_reminder(text)
    if "#напоминание" not in hashtags or reminder_date is None:
        return
    note = add_note(channel_id, cleaned_text, " ".join(hashtags), reminder_date)
    reply = f"✅ Напоминание сохранено: '{note.text}' на {note.reminder_date.astimezone(ZoneInfo('Europe/Moscow')).strftime('%H:%M %d-%m-%Y')}"
    await update.channel_post.reply_text(reply)
    logger.info(f"Saved reminder from channel: {note.text}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот для напоминаний.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = "/start - начало работы\n/help - помощь\n/upcoming - список напоминаний"
    await update.message.reply_text(help_text)

async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    notes = get_upcoming_reminders_window(now, now + timedelta(days=30), only_unsent=False)
    if not notes:
        await update.message.reply_text("Нет предстоящих напоминаний.")
        return
    messages = [f"🔔 {note.text} - назначено на {note.reminder_date.astimezone(ZoneInfo('Europe/Moscow')).strftime('%H:%M %d-%m-%Y')}" for note in notes]
    await update.message.reply_text("\n".join(messages))

async def check_reminders():
    now = datetime.now(ZoneInfo("Europe/Moscow"))
    upcoming = get_upcoming_reminders_window(now, now + timedelta(days=1))
    for note in upcoming:
        try:
            await application.bot.send_message(
                chat_id=note.user_id,
                text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.astimezone(ZoneInfo('Europe/Moscow')).strftime('%H:%M %d-%m-%Y')}"
            )
            mark_reminder_sent(note.id)
            logger.info(f"Sent reminder: {note.text}")
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")

application = Application.builder().token(BOT_TOKEN).build()

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_private_message))
application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.CHANNEL, handle_channel_post))
application.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
application.add_handler(CommandHandler("help", help_command, filters=filters.ChatType.PRIVATE))
application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))

scheduler = AsyncIOScheduler()
scheduler.add_job(check_reminders, "interval", minutes=1)
scheduler.start()

async def health(request):
    return web.Response(text="OK")

async def main():
    runner = web.AppRunner(web.Application())
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBHOOK_PORT + 1)
    await site.start()
    logger.info(f"Health-check server started on port {WEBHOOK_PORT + 1}")

    if USE_WEBHOOK:
        await application.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            webhook_url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET
        )
    else:
        await application.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
