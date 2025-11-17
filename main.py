# main.py
import os
import re
import logging
import calendar
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from typing import Tuple, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from dotenv import load_dotenv

# предполагается, что в database.py есть функции:
# init_db(), add_note(chat_id, text, hashtags, remind_utc), get_upcoming_reminders_window(...)
from database import init_db, add_note, get_upcoming_reminders_window

# -------------------- Настройка логов и окружения --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
WEBHOOK_PORT = int(os.environ.get("PORT", 10000))
TZ_NAME = os.environ.get("TZ", "Europe/Moscow")
APP_TZ = ZoneInfo(TZ_NAME)

if not all([BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET]):
    raise ValueError("Не заданы переменные окружения: BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET_TOKEN")

# -------------------- Инициализация БД --------------------
try:
    init_db()
    logger.info("Database initialized.")
except Exception:
    logger.exception("DB init failed")
    raise

# -------------------- Conversation states --------------------
STATE_CHOOSE_DATE, STATE_INPUT_TIME, STATE_INPUT_TEXT, STATE_CONFIRM = range(4)

# -------------------- Локализация --------------------
RU_MONTHS = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}
WEEK_DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# -------------------- Утилиты --------------------
def parse_hashtags(text: str) -> str:
    tags = re.findall(r"#[\wа-яА-ЯёЁ]+", text)
    return " ".join(tags)

def month_matrix(year: int, month: int) -> List[List[int]]:
    cal = calendar.Calendar(firstweekday=0)
    return cal.monthdayscalendar(year, month)

def add_months(year: int, month: int, delta: int) -> Tuple[int, int]:
    total = (year * 12 + (month - 1)) + delta
    new_year = total // 12
    new_month = (total % 12) + 1
    return new_year, new_month

def build_two_month_calendar(left_year: int, left_month: int, min_date: date, max_date: date) -> InlineKeyboardMarkup:
    """
    Рисует два месяца рядом: left_month (year) и следующий месяц (right).
    Каждая строка клавиатуры содержит два кнопочных блока — дни левого и правого месяцев.
    Callback data для дня: DAY#YYYY#MM#DD
    Навигация: TWO_CAL_PREV#YYYY#MM  и TWO_CAL_NEXT#YYYY#MM (где YYYY/MM — левый месяц)
    """
    right_year, right_month = add_months(left_year, left_month, 1)

    left_matrix = month_matrix(left_year, left_month)
    right_matrix = month_matrix(right_year, right_month)

    # ensure both matrices have same number of weeks (usually 5 or 6)
    max_weeks = max(len(left_matrix), len(right_matrix))
    while len(left_matrix) < max_weeks:
        left_matrix.append([0]*7)
    while len(right_matrix) < max_weeks:
        right_matrix.append([0]*7)

    keyboard = []

    # header: navigation + month names
    header = [
        InlineKeyboardButton("<<", callback_data=f"TWO_CAL_PREV#{left_year}#{left_month}"),
        InlineKeyboardButton(f"{RU_MONTHS[left_month]} {left_year}", callback_data="IGNORE"),
        InlineKeyboardButton(" ", callback_data="IGNORE"),
        InlineKeyboardButton(f"{RU_MONTHS[right_month]} {right_year}", callback_data="IGNORE"),
        InlineKeyboardButton(">>", callback_data=f"TWO_CAL_NEXT#{left_year}#{left_month}")
    ]
    keyboard.append(header)

    # weekday headers (two months side by side)
    wd_row = []
    for wd in WEEK_DAYS_RU:
        wd_row.append(InlineKeyboardButton(wd, callback_data="IGNORE"))
    # duplicate for right month (we'll present them in same row as a visual trick)
    # because keyboard rows are single list, we'll put 7 left-day headers then 7 right-day headers in subsequent rows,
    # but Telegram displays all buttons sequentially — to mimic two calendars we will construct rows combining left/right days.
    # For better alignment build rows combining left-day and right-day buttons per week below.

    # we won't append wd_row as one row; instead create a combined row of placeholders
    keyboard.append([InlineKeyboardButton(w, callback_data="IGNORE") for w in WEEK_DAYS_RU] +
                    [InlineKeyboardButton(w, callback_data="IGNORE") for w in WEEK_DAYS_RU])

    # For each week, create a row that contains 14 buttons: 7 for left month, 7 for right month.
    for week_idx in range(max_weeks):
        left_week = left_matrix[week_idx]
        right_week = right_matrix[week_idx]
        row = []
        # left month days
        for d in left_week:
            if d == 0:
                row.append(InlineKeyboardButton(" ", callback_data="IGNORE"))
            else:
                day_date = date(left_year, left_month, d)
                if day_date < min_date or day_date > max_date:
                    row.append(InlineKeyboardButton(str(d), callback_data="IGNORE"))
                else:
                    row.append(InlineKeyboardButton(str(d), callback_data=f"DAY#{left_year}#{left_month}#{d}"))
        # spacer between months
        row.append(InlineKeyboardButton(" ", callback_data="IGNORE"))
        # right month days
        for d in right_week:
            if d == 0:
                row.append(InlineKeyboardButton(" ", callback_data="IGNORE"))
            else:
                day_date = date(right_year, right_month, d)
                if day_date < min_date or day_date > max_date:
                    row.append(InlineKeyboardButton(str(d), callback_data="IGNORE"))
                else:
                    row.append(InlineKeyboardButton(str(d), callback_data=f"DAY#{right_year}#{right_month}#{d}"))
        keyboard.append(row)

    # bottom row: cancel
    keyboard.append([InlineKeyboardButton("Отмена", callback_data="CANCEL")])
    return InlineKeyboardMarkup(keyboard)

async def send_and_track(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup=None):
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    ud = context.user_data
    ud.setdefault("msg_ids", []).append(msg.message_id)
    return msg

async def cleanup_messages(context: ContextTypes.DEFAULT_TYPE, keep_final: bool = True):
    ud = context.user_data
    chat_id = ud.get("dialog_chat_id")
    if not chat_id:
        return
    final_id = ud.get("final_message_id") if keep_final else None
    ids = ud.get("msg_ids", [])[:]
    for mid in ids:
        try:
            if final_id and mid == final_id:
                continue
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass
    ud["msg_ids"] = []
    return

# -------------------- Handlers --------------------

# Обработчик channel_post: ловит /notify и публикует deep-link; также старый формат #напоминание
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or not update.channel_post.text:
        return
    text = update.channel_post.text.strip()
    chat_id = update.channel_post.chat.id

    if text.startswith("/notify"):
        bot_username = context.bot.username
        if not bot_username:
            try:
                me = await context.bot.get_me()
                bot_username = me.username
            except Exception:
                bot_username = None
        if not bot_username:
            await update.channel_post.reply_text("Ошибка: не могу определить username бота.")
            return
        start_param = f"notify_{chat_id}"
        deep_link = f"https://t.me/{bot_username}?start={start_param}"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Создать в личных сообщениях", url=deep_link)]])
        await update.channel_post.reply_text("Нажмите, чтобы создать интерактивное напоминание в личных сообщениях бота:", reply_markup=kb)
        logger.info(f"Posted deep link for channel {chat_id}: {deep_link}")
        return

    # старый формат
    hashtags = re.findall(r"#[\wа-яА-ЯёЁ]+", text)
    dt_match = re.search(r"@(\d{2}:\d{2}) (\d{2}-\d{2}-\d{4})", text)
    if "#напоминание" not in hashtags or not dt_match:
        logger.info("Channel post ignored (no #напоминание or no date).")
        return

    try:
        time_str, date_str = dt_match.groups()
        naive_dt = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
        event_date = naive_dt.replace(tzinfo=APP_TZ)
        now = datetime.now(APP_TZ)
        if event_date < now + timedelta(days=1):
            await update.channel_post.reply_text("❌ Дата события должна быть хотя бы через сутки.")
            return
        remind_at = event_date - timedelta(days=1)
        remind_utc = remind_at.astimezone(ZoneInfo("UTC"))
        cleaned_text = re.sub(r"#[\wа-яА-ЯёЁ]+", "", text).replace(dt_match.group(0), "").strip()
        text_with_event = f"{cleaned_text} (событие: {event_date.strftime('%H:%M %d-%m-%Y')})"
        add_note(chat_id, text_with_event, " ".join(hashtags), remind_utc)
        await update.channel_post.reply_text(f"✅ Напоминание сохранено.\nУведомление: {remind_at.strftime('%H:%M %d-%m-%Y')}")
        logger.info(f"Saved channel reminder: {cleaned_text}")
    except Exception:
        logger.exception("Error saving channel reminder")
        await update.channel_post.reply_text("Ошибка при сохранении напоминания.")

# /start handler — если пришёл deep-link notify_{channel_id} — стартуем диалог
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    payload = args[0] if args else None
    chat_id = update.effective_chat.id

    if payload and payload.startswith("notify_"):
        try:
            channel_id = int(payload.split("_", 1)[1])
        except Exception:
            await update.message.reply_text("Неправильный параметр запуска.")
            return

        context.user_data.clear()
        context.user_data["target_channel_id"] = channel_id
        context.user_data["dialog_chat_id"] = chat_id
        context.user_data["msg_ids"] = []

        # min/max
        today = date.today()
        min_date = today + timedelta(days=1)
        max_date = today + timedelta(days=365)

        # left month -> current month
        left_year = min_date.year
        left_month = min_date.month

        kb = build_two_month_calendar(left_year, left_month, min_date, max_date)
        await send_and_track(context, chat_id, "Выберите дату события (два месяца):", reply_markup=kb)
        return STATE_CHOOSE_DATE

    await update.message.reply_text("Привет! Для создания напоминания используйте deep-link из канала или /start notify_<channel_id>.")

# CallbackQuery для двухмесячного календаря: навигация или выбор дня
async def callback_two_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "IGNORE":
        return

    if data == "CANCEL":
        await query.edit_message_text("Диалог отменён.")
        await cleanup_messages(context)
        return ConversationHandler.END

    if data.startswith("TWO_CAL_PREV#") or data.startswith("TWO_CAL_NEXT#"):
        _, year_str, month_str = data.split("#")
        year, month = int(year_str), int(month_str)
        delta = -1 if data.startswith("TWO_CAL_PREV#") else 1
        new_year, new_month = add_months(year, month, delta)

        today = date.today()
        min_date = today + timedelta(days=1)
        max_date = today + timedelta(days=365)

        kb = build_two_month_calendar(new_year, new_month, min_date, max_date)
        await query.edit_message_text("Выберите дату события (два месяца):", reply_markup=kb)
        return STATE_CHOOSE_DATE

    if data.startswith("DAY#"):
        _, y_str, m_str, d_str = data.split("#")
        y, m, d = int(y_str), int(m_str), int(d_str)
        chosen = date(y, m, d)

        today = date.today()
        min_date = today + timedelta(days=1)
        max_date = today + timedelta(days=365)
        if chosen < min_date or chosen > max_date:
            await query.edit_message_text("Выбранная дата вне допустимого диапазона. Выберите другую дату.")
            return STATE_CHOOSE_DATE

        context.user_data["event_date"] = chosen
        await query.edit_message_text(f"Вы выбрали: {chosen.strftime('%d-%m-%Y')}\n\nВведите время в формате HH:MM (например, 14:30):")
        return STATE_INPUT_TIME

    return

# Ввод времени вручную
async def input_time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id

    m = re.match(r"^([0-2]?\d):([0-5]\d)$", text)
    if not m:
        await update.message.reply_text("Неверный формат времени. Введите в формате HH:MM (например, 09:05 или 21:30).")
        return STATE_INPUT_TIME

    hour = int(m.group(1))
    minute = int(m.group(2))
    if hour > 23:
        await update.message.reply_text("Час должен быть от 00 до 23.")
        return STATE_INPUT_TIME

    ev_date = context.user_data.get("event_date")
    if not ev_date:
        await update.message.reply_text("Ошибка: дата не найдена. Запустите диалог заново.")
        return ConversationHandler.END

    dt = datetime(ev_date.year, ev_date.month, ev_date.day, hour, minute, tzinfo=APP_TZ)
    now = datetime.now(APP_TZ)
    if dt < now + timedelta(days=1):
        await update.message.reply_text("Время события должно быть не ранее, чем через 24 часа. Введите другое время.")
        return STATE_INPUT_TIME

    context.user_data["event_hour"] = hour
    context.user_data["event_minute"] = minute

    await send_and_track(context, chat_id, "Введите текст напоминания (одно сообщение):")
    return STATE_INPUT_TEXT

# Ввод текста напоминания (ручной)
async def input_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id

    if not text:
        await update.message.reply_text("Пустое сообщение. Введите текст напоминания.")
        return STATE_INPUT_TEXT

    context.user_data["event_text"] = text

    ev_date = context.user_data.get("event_date")
    hour = context.user_data.get("event_hour")
    minute = context.user_data.get("event_minute")
    dt = datetime(ev_date.year, ev_date.month, ev_date.day, hour, minute, tzinfo=APP_TZ)

    preview = (
        f"Проверьте напоминание:\n\n"
        f"Текст: {text}\n"
        f"Когда: {dt.strftime('%H:%M %d-%m-%Y')}\n"
        f"Куда: канал (id {context.user_data.get('target_channel_id')})\n\n"
        f"Нажмите Подтвердить — чтобы сохранить. Или Отмена."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Подтвердить ✅", callback_data="CONFIRM_SAVE"),
         InlineKeyboardButton("Отмена ❌", callback_data="CANCEL")]
    ])
    msg = await send_and_track(context, chat_id, preview, reply_markup=kb)
    context.user_data["final_message_id"] = msg.message_id
    return STATE_CONFIRM

# Подтверждение и сохранение
async def callback_confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "CANCEL":
        await query.edit_message_text("Диалог отменён.")
        await cleanup_messages(context)
        return ConversationHandler.END

    if data == "CONFIRM_SAVE":
        ev_date = context.user_data.get("event_date")
        hour = context.user_data.get("event_hour")
        minute = context.user_data.get("event_minute")
        text = context.user_data.get("event_text", "").strip()
        channel_id = context.user_data.get("target_channel_id")
        if not all([ev_date, hour is not None, minute is not None, text, channel_id]):
            await query.edit_message_text("Ошибка: неполные данные. Попробуйте снова.")
            await cleanup_messages(context)
            return ConversationHandler.END

        event_dt = datetime(ev_date.year, ev_date.month, ev_date.day, hour, minute, tzinfo=APP_TZ)
        remind_at = event_dt - timedelta(days=1)
        remind_utc = remind_at.astimezone(ZoneInfo("UTC"))

        hashtags = parse_hashtags(text)
        if "#напоминание" not in hashtags.split():
            if hashtags:
                hashtags = (hashtags + " #напоминание").strip()
            else:
                hashtags = "#напоминание"

        text_with_event = f"{text} (событие: {event_dt.strftime('%H:%M %d-%m-%Y')})"

        try:
            add_note(channel_id, text_with_event, hashtags, remind_utc)
        except Exception:
            logger.exception("Error saving note to DB")
            await query.edit_message_text("Ошибка при сохранении в БД.")
            await cleanup_messages(context)
            return ConversationHandler.END

        await query.edit_message_text("✅ Напоминание сохранено. Финальное подтверждение в личных сообщениях.")

        final = await context.bot.send_message(
            chat_id=context.user_data.get("dialog_chat_id"),
            text=(
                "Новое напоминание создано:\n"
                f"«{text}»\n"
                f"Дата события: {event_dt.strftime('%H:%M %d-%m-%Y')}\n"
                f"{hashtags}"
            )
        )
        context.user_data["final_message_id"] = final.message_id

        # Отправляем компактное уведомление в канал (без "назначено на", только время)
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=(
                    "🔔 Новое напоминание создано:\n"
                    f"«{text}»\n"
                    f"Время события: {event_dt.strftime('%H:%M')}\n"
                    f"{hashtags}"
                )
            )
        except Exception:
            logger.warning(f"Не удалось отправить подтверждение в канал {channel_id}")

        await cleanup_messages(context, keep_final=True)
        return ConversationHandler.END

    return

# Отмена текстом
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Диалог отменён.")
    await cleanup_messages(context)
    return ConversationHandler.END

# /upcoming в ЛС
async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now_utc = datetime.now(ZoneInfo("UTC"))
    future_utc = now_utc + timedelta(days=365)
    try:
        notes = get_upcoming_reminders_window(now_utc, future_utc, only_unsent=True)
        if not notes:
            await update.message.reply_text("Нет предстоящих напоминаний.")
            return
        lines = ["🔔 Предстоящие напоминания:"]
        for n in notes[:15]:
            d = n.reminder_date.astimezone(APP_TZ)
            lines.append(f"• «{n.text}» — {d.strftime('%H:%M %d-%m-%Y')}")
        await update.message.reply_text("\n".join(lines))
    except Exception:
        logger.exception("Error fetching upcoming notes")
        await update.message.reply_text("Ошибка при получении напоминаний.")

# -------------------- MAIN --------------------
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # channel_post handler
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

    # Conversation for personal dialog
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            STATE_CHOOSE_DATE: [
                CallbackQueryHandler(callback_two_calendar, pattern=r"^(TWO_CAL_PREV#|TWO_CAL_NEXT#|DAY#|IGNORE|CANCEL).*$")
            ],
            STATE_INPUT_TIME: [
                MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, input_time_handler)
            ],
            STATE_INPUT_TEXT: [
                MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, input_text_handler),
                CommandHandler("cancel", cancel_handler)
            ],
            STATE_CONFIRM: [
                CallbackQueryHandler(callback_confirm_save, pattern=r"^(CONFIRM_SAVE|CANCEL)$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        per_user=True,
        allow_reentry=True,
        conversation_timeout=60*30
    )

    application.add_handler(conv)
    application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))

    logger.info("Starting webhook...")
    application.run_webhook(
        listen="0.0.0.0",
        port=WEBHOOK_PORT,
        url_path="/telegram",
        webhook_url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=["message", "edited_message", "channel_post", "edited_channel_post", "callback_query", "my_chat_member", "chat_member"]
    )

if __name__ == "__main__":
    main()
