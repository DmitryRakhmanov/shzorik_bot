import os
import re
import logging
import asyncio
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
from telegram.error import BadRequest
from dotenv import load_dotenv

from database import init_db, add_note, get_upcoming_reminders_window

# --- Состояния диалога ---
DATE, TIME, TEXT, CONFIRM = range(4)

# --- Настройка Логирования и Конфигурации ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
load_dotenv() 

# Переменные окружения для Webhook
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
WEBHOOK_PORT = int(os.environ.get("PORT", 10000))
TZ_NAME = os.environ.get("TZ", "Europe/Moscow") 
APP_TZ = ZoneInfo(TZ_NAME)

if not all([BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET]):
    raise ValueError("Не заданы все переменные для Webhook (BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET_TOKEN)")

# --- Инициализация БД ---
try:
    init_db()
    logger.info("Database initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize database: {e}. Exiting.")
    exit(1)

# --- Вспомогательные функции ---

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
            
    cleaned_text = re.sub(r"#[а-яА-ЯёЁa-zA-Z0-9_]+", "", text).strip()
    if dt_match:
        cleaned_text = cleaned_text.replace(dt_match.group(0), "").strip()
        
    return cleaned_text, " ".join(hashtags), event_date

# --- Календарь и время ---

def create_calendar(year=None, month=None):
    now = datetime.now(APP_TZ)
    if year is None: year = now.year
    if month is None: month = now.month
    first = date(year, month, 1)
    last = (date(year, month + 1, 1) - timedelta(days=1)) if month < 12 else (date(year + 1, 1, 1) - timedelta(days=1))
    start_weekday = first.weekday()
    month_names = ['Янв','Фев','Мар','Апр','Май','Июн','Июл','Авг','Сен','Окт','Ноя','Дек']

    keyboard = []
    row = []
    if month > 1:
        row.append(InlineKeyboardButton("←", callback_data=f"cal:{year}:{month-1}"))
    else:
        row.append(InlineKeyboardButton("←", callback_data=f"cal:{year-1}:12"))
    row.append(InlineKeyboardButton(f"{month_names[month-1]} {year}", callback_data="ignore"))
    if month < 12:
        row.append(InlineKeyboardButton("→", callback_data=f"cal:{year}:{month+1}"))
    else:
        row.append(InlineKeyboardButton("→", callback_data=f"cal:{year+1}:1"))
    keyboard.append(row)

    keyboard.append([InlineKeyboardButton(d, callback_data="ignore") for d in ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]])

    row = [""] * start_weekday
    for day in range(1, last.day + 1):
        row.append(str(day))
        if len(row) == 7:
            keyboard.append([InlineKeyboardButton(d if d != "" else " ", callback_data=f"cal_day:{year}:{month}:{d}" if d != "" else "ignore") for d in row])
            row = []
    if row:
        row.extend([""] * (7 - len(row)))
        keyboard.append([InlineKeyboardButton(d if d != "" else " ", callback_data=f"cal_day:{year}:{month}:{d}" if d != "" else "ignore") for d in row])

    keyboard.append([InlineKeyboardButton("Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

def create_time_keyboard():
    hours = [InlineKeyboardButton(f"{h:02d}", callback_data=f"time_h:{h:02d}") for h in range(24)]
    minutes = [InlineKeyboardButton(f"{m:02d}", callback_data=f"time_m:{m:02d}") for m in range(0, 60, 5)]
    keyboard = [hours[i:i+6] for i in range(0, 24, 6)]
    keyboard += [minutes[i:i+6] for i in range(0, 12, 6)]
    keyboard.append([InlineKeyboardButton("Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

# --- Хендлеры ---

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or not update.channel_post.text:
        return
        
    text = update.channel_post.text
    chat_id = update.channel_post.chat.id
    
    cleaned_text, hashtags, event_date = parse_reminder(text)
    
    if "#напоминание" not in hashtags or event_date is None:
        logger.info("Ignoring post: no #напоминание or valid date found.")
        return
    
    now = datetime.now(APP_TZ)
    if event_date < now + timedelta(days=1):
        await update.channel_post.reply_text("❌ Дата события должна быть хотя бы через сутки.")
        return
        
    try:
        remind_at = event_date - timedelta(days=1)
        remind_at_utc = remind_at.astimezone(ZoneInfo("UTC"))
        
        text_with_event = f"{cleaned_text} (событие: {event_date.strftime('%H:%M %d-%m-%Y')})"
        
        note = add_note(chat_id, text_with_event, hashtags, remind_at_utc)
        
        remind_date_str = remind_at.strftime('%H:%M %d-%m-%Y')
        event_date_str = event_date.strftime('%H:%M %d-%m-%Y')
        reply = f"✅ Напоминание сохранено: «{cleaned_text}»\nБудет уведомлено за сутки ({remind_date_str}) о событии {event_date_str}"
        await update.channel_post.reply_text(reply)
        logger.info(f"Saved reminder for channel {chat_id}: {note.text}")
        
    except Exception as e:
        logger.error(f"Error in handle_channel_post: {e}")
        await update.channel_post.reply_text(f"❌ Ошибка: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот для напоминаний. Используйте /upcoming для просмотра.")

async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now_utc = datetime.now(ZoneInfo("UTC"))
    end_of_time = now_utc + timedelta(days=365)
    
    try:
        notes = get_upcoming_reminders_window(now_utc, end_of_time, only_unsent=True)
        
        if not notes:
            await update.message.reply_text("Нет предстоящих неотправленных напоминаний.")
            return
            
        messages = ["🔔 Предстоящие напоминания:"]
        for note in notes:
            reminder_date_local = note.reminder_date.astimezone(APP_TZ)
            messages.append(
                f"• «{note.text}» - {reminder_date_local.strftime('%H:%M %d-%m-%Y')}"
            )
        await update.message.reply_text("\n".join(messages[:15])) 
        
    except Exception as e:
        logger.error(f"Error fetching upcoming notes: {e}")
        await update.message.reply_text(f"❌ Ошибка получения напоминаний: {e}")

# --- Диалог /notify ---

async def start_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    context.user_data.clear()
    context.user_data["channel_id"] = chat_id
    context.user_data["messages_to_delete"] = []

    msg = await context.bot.send_message(chat_id=chat_id, text="Выберите дату события:", reply_markup=create_calendar())
    context.user_data["messages_to_delete"].append(msg.message_id)
    return DATE

# --- Оставьте select_date, select_time, enter_text, confirm из предыдущей версии ---

# --- Запуск ---
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Старый формат с #напоминание
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.CHANNEL, handle_channel_post))

    # /notify
    application.add_handler(CommandHandler("notify", start_notify, filters=filters.ChatType.CHANNEL))

    # ЛС команды
    application.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))

    # ConversationHandler для /notify
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("notify", start_notify, filters=filters.ChatType.CHANNEL)],
        states={
            DATE: [CallbackQueryHandler(select_date)],
            TIME: [CallbackQueryHandler(select_time)],
            TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.CHANNEL, enter_text)],
            CONFIRM: [CallbackQueryHandler(confirm)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END, filters=filters.ChatType.CHANNEL)],
        per_chat=True,
        per_message=False,
        allow_reentry=True,
    )

    application.add_handler(conv_handler)

    logger.info("Starting bot with webhooks...")
    application.run_webhook(
        listen="0.0.0.0",
        port=WEBHOOK_PORT,
        url_path="/telegram",
        webhook_url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )

if __name__ == "__main__":
    main()