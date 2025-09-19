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
    update_note_reminder_date,
    get_upcoming_reminders_window,
    initialize_db,
)

# ------------------- Логирование -------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.info(f"Using python-telegram-bot version: {TG_BOT_VERSION}")

# ------------------- Команды -------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_chat.send_message(
        f"Привет, {user.mention_html()}! Я бот для заметок.\n"
        "Чтобы сохранить заметку, просто отправь текст.\n"
        "Для добавления хэштегов используй #хештег.\n"
        "Для напоминания формат: 'текст заметки #тег @ЧЧ:ММ ДД-ММ-ГГГГ'.\n"
        "Напоминания приходят в канал за день.\n"
        "Команды:\n"
        "/find #хештег - найти заметки\n"
        "/all_notes - все ваши заметки\n"
        "/upcoming_notes - предстоящие напоминания\n"
        "/help - это сообщение",
        parse_mode=ParseMode.HTML
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)

# ------------------- Обработка сообщений -------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_obj = update.effective_message
    if not message_obj or not message_obj.text:
        return

    user_id = message_obj.from_user.id if message_obj.from_user else None
    text = message_obj.text

    if text.startswith('/'):
        return  # игнорируем команды

    hashtags_str = None
    reminder_date = None
    reminder_string_found = None

    # ------------------- Парсинг @HH:MM DD-MM-YYYY -------------------
    full_pattern = r"@(\d{2}:\d{2})\s+(\d{2}-\d{2}-\d{4})"
    match_full = re.search(full_pattern, text)
    tz_str = os.environ.get("TIMEZONE", "Europe/Moscow")

    if match_full:
        time_str, date_str = match_full.groups()
        try:
            reminder_date = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
            reminder_date = reminder_date.replace(tzinfo=ZoneInfo(tz_str))
            reminder_string_found = match_full.group(0)
        except Exception as e:
            logger.error(f"Ошибка парсинга даты/времени: {e}")
            await message_obj.reply_text("Неверный формат даты/времени. Используйте @ЧЧ:ММ ДД-ММ-ГГГГ")
            return
    else:
        date_only_pattern = r"@(\d{2}-\d{2}-\d{4})"
        match_date = re.search(date_only_pattern, text)
        if match_date:
            date_str = match_date.group(1)
            try:
                reminder_date = datetime.strptime(date_str, "%d-%m-%Y").replace(hour=9, minute=0)
                reminder_date = reminder_date.replace(tzinfo=ZoneInfo(tz_str))
                reminder_string_found = match_date.group(0)
            except Exception as e:
                logger.error(f"Ошибка парсинга даты: {e}")
                await message_obj.reply_text("Неверный формат даты. Используйте @ДД-ММ-ГГГГ")
                return

    # ------------------- Парсинг хэштегов -------------------
    cleaned_text = text
    if reminder_string_found:
        cleaned_text = re.sub(re.escape(reminder_string_found), '', cleaned_text).strip()

    hashtags = re.findall(r"#(\w+)", cleaned_text)
    hashtags_lower = [h.lower() for h in hashtags]
    hashtags_str = " ".join(hashtags_lower) if hashtags else None

    note_text = re.sub(r"#\w+", "", cleaned_text).strip()
    if not note_text:
        await message_obj.reply_text("Введите текст заметки.")
        return

    # ------------------- Сохраняем только #напоминание для каналов -------------------
    if update.channel_post and "напоминание" not in hashtags_lower:
        return

    add_note(user_id, note_text, hashtags_str, reminder_date)

    response = "Заметка сохранена!"
    if hashtags_str:
        response += f"\nХэштеги: #{hashtags_str.replace(' ', ', #')}"
    if reminder_date:
        response += f"\nНапоминание на: {reminder_date.strftime('%H:%M %d-%m-%Y')}"
        response += "\nБот уведомит в канале за день до события."

    if not update.channel_post:
        await message_obj.reply_text(response)

# ------------------- Команды поиска -------------------
async def find_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    message_obj = update.effective_message
    if not context.args:
        await message_obj.reply_text("Укажите хэштег. Пример: /find #важно")
        return

    hashtag = context.args[0].lower()
    if hashtag.startswith('#'):
        hashtag = hashtag[1:]

    notes = find_notes_by_user_and_hashtag(user_id, hashtag)
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
    else:
        response = "У вас пока нет заметок."
    await message_obj.reply_text(response)

async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message_obj = update.effective_message
    now = datetime.now(tz=ZoneInfo(os.environ.get("TIMEZONE", "Europe/Moscow")))
    notes = get_upcoming_reminders_window(now, now + timedelta(days=1))
    if notes:
        response = "📅 Предстоящие напоминания:\n"
        for i, note in enumerate(notes):
            response += f"{i+1}. {note.text}"
            if note.hashtags:
                response += f" (Хэштеги: #{note.hashtags.replace(' ', ', #')})"
            if note.reminder_date:
                response += f" (Напоминание: {note.reminder_date.strftime('%H:%M %d-%m-%Y')})"
            response += "\n"
    else:
        response = "Нет предстоящих напоминаний."
    await message_obj.reply_text(response)

# ------------------- Проверка напоминаний -------------------
async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    tz_str = os.environ.get("TIMEZONE", "Europe/Moscow")
    now = datetime.now(tz=ZoneInfo(tz_str))
    window_end = now + timedelta(days=1)
    reminders = get_upcoming_reminders_window(now, window_end)

    channel_id_str = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not channel_id_str:
        logger.error("TELEGRAM_CHANNEL_ID не задан.")
        return
    try:
        channel_id = int(channel_id_str)
    except ValueError:
        logger.error("TELEGRAM_CHANNEL_ID должен быть числом.")
        return

    for note in reminders:
        try:
            if note.reminder_date:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=f"🔔 Напоминание: '{note.text}' назначено на {note.reminder_date.strftime('%H:%M %d-%m-%Y')}."
                )
                logger.info(f"Отправлено уведомление для заметки {note.id}")
                # Сбрасываем дату напоминания, чтобы не отправлять снова
                update_note_reminder_date(note.id, None)
        except Exception as e:
            logger.error(f"Ошибка отправки напоминания для {note.id}: {e}")

# ------------------- Main -------------------
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

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("find", find_notes_command))
    application.add_handler(CommandHandler("all_notes", all_notes_command))
    application.add_handler(CommandHandler("upcoming_notes", upcoming_notes_command))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & (filters.UpdateType.MESSAGE | filters.UpdateType.CHANNEL_POST),
            handle_message
        )
    )

    # ------------------- Job Queue -------------------
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=300, first=0)  # каждые 5 минут

    # ------------------- Webhook -------------------
    logger.info(f"Starting webhook on port {PORT}")
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
