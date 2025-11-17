import os
import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from dotenv import load_dotenv

from database import init_db, add_note, get_upcoming_reminders_window

# -------------------- ЛОГИ --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

# -------------------- CONFIG --------------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
WEBHOOK_PORT = int(os.environ.get("PORT", 10000))
TZ_NAME = os.environ.get("TZ", "Europe/Moscow")
APP_TZ = ZoneInfo(TZ_NAME)

if not all([BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET]):
    raise ValueError("Не заданы переменные окружения: BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET_TOKEN")


# -------------------- INIT DATABASE --------------------
try:
    init_db()
    logger.info("Database initialized.")
except Exception as e:
    logger.error(f"DB init failed: {e}")
    exit(1)


# -------------------- UTILS --------------------
def parse_reminder(text: str):
    hashtags = re.findall(r"#[а-яА-ЯёЁa-zA-Z0-9_]+", text)
    dt_match = re.search(r"@(\d{2}:\d{2}) (\d{2}-\d{2}-\d{4})", text)

    event_date = None
    if dt_match:
        time_str, date_str = dt_match.groups()
        try:
            naive_dt = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
            event_date = naive_dt.replace(tzinfo=APP_TZ)
        except ValueError:
            return text, " ".join(hashtags), None

    cleaned = re.sub(r"#[а-яА-ЯёЁa-zA-Z0-9_]+", "", text).strip()
    if dt_match:
        cleaned = cleaned.replace(dt_match.group(0), "").strip()

    return cleaned, " ".join(hashtags), event_date


# -------------------- HANDLERS --------------------
async def start_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает /notify и в ЛС, и в канале."""
    chat = update.effective_chat
    logger.info(f"/notify received from chat {chat.id}, type={chat.type}")

    await update.effective_message.reply_text(
        "📅 Давайте создадим напоминание!\n"
        "➡ Выбор даты, времени и текста будет здесь.\n"
        "⚠ Диалог пока в демо-режиме."
    )


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает ВСЕ channel_post (сообщения в канале)."""
    if not update.channel_post:
        return

    text = update.channel_post.text or ""
    chat_id = update.channel_post.chat.id

    # -------------------- ЛОВИМ /notify --------------------
    if text.strip() == "/notify":
        logger.info(f"Trigger /notify in channel {chat_id}")
        return await start_notify(update, context)

    # -------------------- ОБРАБОТКА СТАРОГО ФОРМАТА --------------------
    cleaned_text, hashtags, event_date = parse_reminder(text)

    if "#напоминание" not in hashtags or event_date is None:
        logger.info("Ignoring channel post — no #напоминание or invalid date.")
        return

    now = datetime.now(APP_TZ)
    if event_date < now + timedelta(days=1):
        await update.channel_post.reply_text("❌ Дата события должна быть хотя бы через сутки.")
        return

    try:
        remind_at = event_date - timedelta(days=1)
        remind_utc = remind_at.astimezone(ZoneInfo("UTC"))

        text_with_event = f"{cleaned_text} (событие: {event_date.strftime('%H:%M %d-%m-%Y')})"

        add_note(chat_id, text_with_event, hashtags, remind_utc)

        await update.channel_post.reply_text(
            f"✅ Напоминание сохранено!\n"
            f"Будет уведомлено за сутки: {remind_at.strftime('%H:%M %d-%m-%Y')}"
        )
        logger.info(f"Saved reminder for channel {chat_id}: {cleaned_text}")

    except Exception as e:
        logger.error(f"Error saving note: {e}")
        await update.channel_post.reply_text(f"❌ Ошибка сохранения: {e}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я бот для напоминаний.\n"
        "Команда /upcoming покажет будущие напоминания."
    )


async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now_utc = datetime.now(ZoneInfo("UTC"))
    future_utc = now_utc + timedelta(days=365)

    try:
        notes = get_upcoming_reminders_window(now_utc, future_utc, only_unsent=True)
        if not notes:
            return await update.message.reply_text("Нет предстоящих напоминаний.")

        msg = ["🔔 Предстоящие напоминания:"]
        for n in notes[:15]:
            d = n.reminder_date.astimezone(APP_TZ)
            msg.append(f"• «{n.text}» — {d.strftime('%H:%M %d-%m-%Y')}")

        await update.message.reply_text("\n".join(msg))

    except Exception as e:
        logger.error(f"Error fetching notes: {e}")
        await update.message.reply_text(f"Ошибка: {e}")


# -------------------- MAIN --------------------
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Канал: ВСЁ идёт через один хендлер
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

    # /notify — в ЛС
    application.add_handler(CommandHandler("notify", start_notify, filters.ChatType.PRIVATE))

    # ЛС команды
    application.add_handler(CommandHandler("start", start_command, filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters.ChatType.PRIVATE))

    logger.info("Starting bot via webhook...")

    application.run_webhook(
        listen="0.0.0.0",
        port=WEBHOOK_PORT,
        url_path="/telegram",
        webhook_url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )


if __name__ == "__main__":
    main()
