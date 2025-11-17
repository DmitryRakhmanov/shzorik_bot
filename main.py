import os
import re
import logging
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, MessageHandler, filters, ConversationHandler, CallbackQueryHandler
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

# --- Удаление сообщений ---

async def delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id, message_id):
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except BadRequest as e:
        logger.warning(f"Failed to delete message: {e}")

# --- Календарь ---

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

# --- Время ---

def create_time_keyboard():
    hours = [InlineKeyboardButton(f"{h:02d}", callback_data=f"time_h:{h:02d}") for h in range(24)]
    minutes = [InlineKeyboardButton(f"{m:02d}", callback_data=f"time_m:{m:02d}") for m in range(0, 60, 5)]
    keyboard = [hours[i:i+6] for i in range(0, 24, 6)]
    keyboard += [minutes[i:i+6] for i in range(0, 12, 6)]
    keyboard.append([InlineKeyboardButton("Отмена", callback_data="cancel")])
    return InlineKeyboardMarkup(keyboard)

# --- Диалог /notify ---

async def start_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.channel_post:
        return ConversationHandler.END
    chat_id = update.channel_post.chat.id
    context.user_data.clear()
    context.user_data["channel_id"] = chat_id
    context.user_data["messages_to_delete"] = []

    msg = await update.channel_post.reply_text("Выберите дату события:", reply_markup=create_calendar())
    context.user_data["messages_to_delete"].append(msg.message_id)
    return DATE

async def select_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id
    message_id = query.message.message_id

    if data == "cancel":
        await query.edit_message_text("Создание напоминания отменено.")
        await delete_message(context, chat_id, message_id)
        return ConversationHandler.END

    if data.startswith("cal:"):
        year, month = map(int, data.split(":")[1:])
        await query.edit_message_reply_markup(reply_markup=create_calendar(year, month))
        return DATE

    if data.startswith("cal_day:"):
        _, year, month, day = data.split(":")
        year, month, day = int(year), int(month), int(day)
        context.user_data["event_date"] = date(year, month, day)
        await query.edit_message_text("Выберите время события:", reply_markup=create_time_keyboard())
        return TIME

    return DATE

async def select_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id
    message_id = query.message.message_id

    if data == "cancel":
        await query.edit_message_text("Создание напоминания отменено.")
        await delete_message(context, chat_id, message_id)
        return ConversationHandler.END

    if data.startswith("time_h:"):
        context.user_data["hour"] = int(data.split(":")[1])
        await query.edit_message_reply_markup(reply_markup=create_time_keyboard())
        return TIME

    if data.startswith("time_m:"):
        context.user_data["minute"] = int(data.split(":")[1])
        if "hour" not in context.user_data:
            await query.answer("Сначала выберите час")
            return TIME
        hour = context.user_data["hour"]
        minute = context.user_data["minute"]
        event_date = context.user_data["event_date"]
        event_dt = datetime.combine(event_date, datetime.min.time()).replace(hour=hour, minute=minute, tzinfo=APP_TZ)
        context.user_data["event_dt"] = event_dt
        await query.edit_message_text("Напишите текст напоминания:")
        return TEXT

    return TIME

async def enter_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.channel_post:
        return TEXT
    text = update.channel_post.text.strip()
    if not text:
        await update.channel_post.reply_text("Текст не может быть пустым. Попробуйте снова:")
        return TEXT
    context.user_data["text"] = text

    event_dt = context.user_data["event_dt"]
    remind_dt = event_dt - timedelta(days=1)

    keyboard = [
        [InlineKeyboardButton("Сохранить", callback_data="save")],
        [InlineKeyboardButton("Отмена", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    message = (
        f"Подтвердите:\n"
        f"«{text}»\n"
        f"Событие: {event_dt.strftime('%H:%M %d-%m-%Y')}\n"
        f"Напоминание: за 24ч ({remind_dt.strftime('%H:%M %d-%m-%Y')})"
    )
    msg = await update.channel_post.reply_text(message, reply_markup=reply_markup)
    context.user_data["messages_to_delete"].append(msg.message_id)

    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat.id
    message_id = query.message.message_id

    if data == "cancel":
        await query.edit_message_text("Создание напоминания отменено.")
        return ConversationHandler.END

    if data == "save":
        channel_id = context.user_data["channel_id"]
        text = context.user_data["text"]
        event_dt = context.user_data["event_dt"]
        remind_dt_utc = (event_dt - timedelta(days=1)).astimezone(ZoneInfo("UTC"))

        note = add_note(channel_id, text, "#напоминание", remind_dt_utc)

        final_message = (
            f"Напоминание сохранено! «{text}»\n"
            f"Событие: {event_dt.strftime('%H:%M %d-%m-%Y')}\n"
            f"Напоминание: за 24ч ({(event_dt - timedelta(days=1)).strftime('%H:%M %d-%m-%Y')})"
        )
        await query.edit_message_text(final_message)

        # Удаляем все промежуточные сообщения
        for msg_id in context.user_data["messages_to_delete"][:-1]:
            await delete_message(context, chat_id, msg_id)

        return ConversationHandler.END

    return CONFIRM

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот для напоминаний. Используйте /upcoming для просмотра предстоящих напоминаний.")

async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /upcoming"""
    now_utc = datetime.now(ZoneInfo("UTC"))
    end_of_time = now_utc + timedelta(days=365) # Смотрим на год вперед
    
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

# --- Запуск Бота ---

def main():
    # Создаём Application
    application = Application.builder().token(BOT_TOKEN).build() 
    
    # Хендлер для #напоминание и старого формата
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.CHANNEL, handle_channel_post))
    
    # Хендлер для /notify в канале
    application.add_handler(MessageHandler(filters.Regex(r"^/notify$") & filters.ChatType.CHANNEL, start_notify))
    
    # Команды в ЛС
    application.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))

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
