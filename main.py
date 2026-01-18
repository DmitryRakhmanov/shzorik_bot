# main.py — финальная версия
import os
import re
import logging
import calendar
import asyncio
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from dotenv import load_dotenv

# DB (синхронный) — будем запускать в executor
from database import init_db, add_note, get_upcoming_reminders_window, mark_reminder_sent
# Добавленные импортируемые функции (не меняя старую строку)
from database import get_latest_cactus, upsert_cactus

# -------------------- CONFIG --------------------
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # если задан — webhook mode
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET_TOKEN")
WEBHOOK_PORT = int(os.environ.get("PORT", 10000))
TZ_NAME = os.environ.get("TZ", "Europe/Moscow")
APP_TZ = ZoneInfo(TZ_NAME)

DELETE_DELAY_SECONDS = int(os.environ.get("DELETE_DELAY_SECONDS", 120))

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения: TELEGRAM_BOT_TOKEN или BOT_TOKEN")

# -------------------- DB init --------------------
try:
    init_db()
    logger.info("Database initialized")
except Exception:
    logger.exception("Failed to initialize DB")
    raise

# -------------------- Conversation states --------------------
STATE_CHOOSE_DATE = 0
STATE_INPUT_TIME = 1
STATE_INPUT_TEXT = 2
STATE_CONFIRM = 3

# -------------------- Localization --------------------
RU_MONTHS = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}
WEEK_DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# -------------------- Utilities --------------------
def parse_hashtags(text: str) -> str:
    tags = re.findall(r"#[\wа-яА-ЯёЁ]+", text)
    return " ".join(tags)

def build_month_calendar(year: int, month: int, min_date: date, max_date: date) -> InlineKeyboardMarkup:
    cal = calendar.Calendar(firstweekday=0)
    month_days = cal.monthdayscalendar(year, month)

    keyboard = []
    keyboard.append([
        InlineKeyboardButton("<<", callback_data=f"CAL_PREV#{year}#{month}"),
        InlineKeyboardButton(f"{RU_MONTHS[month]} {year}", callback_data="IGNORE"),
        InlineKeyboardButton(">>", callback_data=f"CAL_NEXT#{year}#{month}")
    ])
    keyboard.append([InlineKeyboardButton(w, callback_data="IGNORE") for w in WEEK_DAYS_RU])

    for week in month_days:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="IGNORE"))
            else:
                day_date = date(year, month, day)
                if day_date < min_date or day_date > max_date:
                    row.append(InlineKeyboardButton(str(day), callback_data="IGNORE"))
                else:
                    row.append(InlineKeyboardButton(str(day), callback_data=f"DAY#{year}#{month}#{day}"))
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton("Отмена", callback_data="CANCEL")])
    return InlineKeyboardMarkup(keyboard)

async def send_and_track(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup: Optional[InlineKeyboardMarkup]=None):
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

async def try_delete_message(bot, chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as e:
        logger.debug(f"Delete failed for {chat_id}:{message_id} — {e}")
        return False

async def schedule_delete(bot, chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    await try_delete_message(bot, chat_id, message_id)

# -------------------- DB wrappers (run sync DB funcs in executor) --------------------
async def db_add_note(user_id: int, text: str, hashtags: str, reminder_date):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, add_note, user_id, text, hashtags, reminder_date)

async def db_get_upcoming(start_time_utc: datetime, end_time_utc: datetime, only_unsent: bool = True):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_upcoming_reminders_window, start_time_utc, end_time_utc, only_unsent)

async def db_mark_reminder_sent(note_id: int):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, mark_reminder_sent, note_id)

# -------------------- New DB wrappers for cactus (async wrappers) --------------------
async def db_get_cactus():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_latest_cactus)

async def db_set_cactus(money: int):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, upsert_cactus, money)

# -------------------- Handlers --------------------
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post:
        return
    text = (update.channel_post.text or "").strip()
    chat = update.channel_post.chat
    chat_id = chat.id
    msg_id = update.channel_post.message_id

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
        bot_msg = await context.bot.send_message(chat_id=chat_id, text="Нажмите, чтобы создать интерактивное напоминание в личных сообщениях бота:", reply_markup=kb)
        bot_msg_id = bot_msg.message_id

        await try_delete_message(context.bot, chat_id, msg_id)
        try:
            asyncio.create_task(schedule_delete(context.bot, chat_id, bot_msg_id, 30))
        except Exception:
            logger.debug("Failed to schedule quick deletion; falling back to scheduled deletion")
        try:
            asyncio.create_task(schedule_delete(context.bot, chat_id, bot_msg_id, DELETE_DELAY_SECONDS))
        except Exception:
            logger.debug("Failed to schedule fallback deletion")
        return

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
            await context.bot.send_message(chat_id=chat_id, text="❌ Дата события должна быть хотя бы через сутки.")
            return
        remind_at = event_date - timedelta(days=1)
        remind_utc = remind_at.astimezone(ZoneInfo("UTC"))
        cleaned_text = re.sub(r"#[\wа-яА-ЯёЁ]+", "", text).replace(dt_match.group(0), "").strip()
        text_with_event = f"{cleaned_text} (событие: {event_date.strftime('%H:%M %d-%m-%Y')})"
        await db_add_note(chat_id, text_with_event, " ".join(hashtags), remind_utc)
        await context.bot.send_message(chat_id=chat_id, text=f"✅ Напоминание сохранено.\nУведомление: {remind_at.strftime('%H:%M %d-%m-%Y')}")
        logger.info(f"Saved channel reminder: {cleaned_text}")
    except Exception:
        logger.exception("Error saving channel reminder")
        await context.bot.send_message(chat_id=chat_id, text="Ошибка при сохранении напоминания.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args or []
    payload = args[0] if args else None
    chat_id = update.effective_chat.id

    if payload and payload.startswith("notify_"):
        try:
            channel_id = int(payload.split("_", 1)[1])
        except Exception:
            await update.message.reply_text("Неверный параметр запуска.")
            return

        context.user_data.clear()
        context.user_data["target_channel_id"] = channel_id
        context.user_data["dialog_chat_id"] = chat_id
        context.user_data["msg_ids"] = []

        today = date.today()
        min_date = today + timedelta(days=1)
        max_date = today + timedelta(days=365)
        left_year = min_date.year
        left_month = min_date.month
        cal_markup = build_month_calendar(left_year, left_month, min_date, max_date)
        await send_and_track(context, chat_id, "Выберите дату события (месячный календарь):", reply_markup=cal_markup)
        return STATE_CHOOSE_DATE

    await update.message.reply_text("Привет! Для создания напоминания команду /notify.")

async def callback_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if data == "IGNORE":
        return

    if data == "CANCEL":
        await query.edit_message_text("Диалог отменён.")
        await cleanup_messages(context)
        return ConversationHandler.END

    if data.startswith("CAL_PREV#") or data.startswith("CAL_NEXT#"):
        _, y, m = data.split("#")
        year, month = int(y), int(m)
        if data.startswith("CAL_PREV#"):
            if month == 1:
                month = 12
                year -= 1
            else:
                month -= 1
        else:
            if month == 12:
                month = 1
                year += 1
            else:
                month += 1
        today = date.today()
        min_date = today + timedelta(days=1)
        max_date = today + timedelta(days=365)
        cal_markup = build_month_calendar(year, month, min_date, max_date)
        await query.edit_message_text("Выберите дату события (месячный календарь):", reply_markup=cal_markup)
        return STATE_CHOOSE_DATE

    if data.startswith("DAY#"):
        _, y, m, d = data.split("#")
        chosen = date(int(y), int(m), int(d))
        today = date.today()
        min_date = today + timedelta(days=1)
        max_date = today + timedelta(days=365)
        if chosen < min_date or chosen > max_date:
            await query.edit_message_text("Выбранная дата вне допустимого диапазона. Выберите другую дату.")
            cal_markup = build_month_calendar(int(y), int(m), min_date, max_date)
            await query.edit_message_text("Выберите дату события (месячный календарь):", reply_markup=cal_markup)
            return STATE_CHOOSE_DATE

        context.user_data["event_date"] = chosen
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="CANCEL")]])
        await query.edit_message_text(f"Вы выбрали: {chosen.strftime('%d-%m-%Y')}\n\nВведите время в формате HH:MM (например, 14:30):", reply_markup=cancel_kb)
        return STATE_INPUT_TIME

    return

# Input time handler - expects HH:MM
async def input_time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id

    m = re.match(r"^([0-2]?\d):([0-5]\d)$", text)
    if not m:
        await update.message.reply_text("Неверный формат времени. Введите в формате HH:MM (например, 09:05 или 21:30).", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="CANCEL")]]))
        return STATE_INPUT_TIME

    hour = int(m.group(1))
    minute = int(m.group(2))
    if hour > 23:
        await update.message.reply_text("Час должен быть от 00 до 23.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="CANCEL")]]))
        return STATE_INPUT_TIME

    ev_date = context.user_data.get("event_date")
    if not ev_date:
        await update.message.reply_text("Ошибка: дата отсутствует. Запустите диалог заново.")
        return ConversationHandler.END

    dt = datetime(ev_date.year, ev_date.month, ev_date.day, hour, minute, tzinfo=APP_TZ)
    now = datetime.now(APP_TZ)
    if dt < now + timedelta(days=1):
        await update.message.reply_text("Время события должно быть не ранее, чем через 24 часа. Введите другое время.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="CANCEL")]]))
        return STATE_INPUT_TIME

    context.user_data["event_hour"] = hour
    context.user_data["event_minute"] = minute

    await send_and_track(context, chat_id, "Введите текст напоминания (одно сообщение):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="CANCEL")]]))
    return STATE_INPUT_TEXT

# Input text handler - user supplies event text
async def input_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    chat_id = update.effective_chat.id

    if not text:
        await update.message.reply_text("Пустое сообщение. Введите текст напоминания.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data="CANCEL")]]))
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

# Confirm and save
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
            await db_add_note(channel_id, text_with_event, hashtags, remind_utc)
        except Exception:
            logger.exception("Failed adding note to DB")
            await query.edit_message_text("Ошибка при сохранении в БД.")
            await cleanup_messages(context)
            return ConversationHandler.END

        await query.edit_message_text("✅ Напоминание сохранено. Финальное подтверждение в личных сообщениях.")

        final = await context.bot.send_message(
            chat_id=context.user_data.get("dialog_chat_id"),
            text=(
                "Новое напоминание создано:\n"
                f"«{text_with_event}»\n"
                f"{hashtags}"
            )
        )
        context.user_data["final_message_id"] = final.message_id

        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=(
                    "🔔 Новое напоминание создано:\n"
                    f"«{text_with_event}»\n"
                    f"{hashtags}"
                )
            )
        except Exception:
            logger.warning(f"Could not post confirmation to channel {channel_id}. Bot may lack post rights.")

        await cleanup_messages(context, keep_final=True)
        return ConversationHandler.END

    return

# Cancel text command
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Диалог отменён.")
    await cleanup_messages(context)
    return ConversationHandler.END

# Simple /upcoming command in private
async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now_utc = datetime.now(ZoneInfo("UTC"))
    future_utc = now_utc + timedelta(days=365)
    try:
        notes = await db_get_upcoming(now_utc, future_utc, only_unsent=True)
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

# -------------------- Reminders job (integrated) --------------------
async def send_reminders_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        now_utc = datetime.now(ZoneInfo("UTC"))
        window_start_utc = now_utc - timedelta(minutes=20)
        window_end_utc = now_utc + timedelta(minutes=5)

        upcoming = await db_get_upcoming(window_start_utc, window_end_utc, only_unsent=True)
        logger.info(f"Reminders job: found {len(upcoming)} reminders in window {window_start_utc}..{window_end_utc}")

        if not upcoming:
            return

        sent_count = 0
        for note in upcoming:
            try:
                message_text = f"🔔 Напоминание:\n«{note.text}»\n"
                await context.bot.send_message(chat_id=note.user_id, text=message_text)
                await db_mark_reminder_sent(note.id)
                logger.info(f"Sent reminder {note.id} to {note.user_id}")
                sent_count += 1
            except Exception as e:
                logger.exception(f"Failed to send reminder {getattr(note, 'id', 'unknown')}: {e}")

        logger.info(f"Reminders job: sent {sent_count} messages")
    except Exception:
        logger.exception("Reminders job failed")

# -------------------- Health test --------------------
async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 Pong! Бот живой.")

# -------------------- New handlers: /cactus and /cactusnew --------------------
async def cactus_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cactus - удаляет сообщение пользователя и отправляет текущую запись из таблицы cactus.
    Сообщение удаляется автоматически через 60 секунд.
    """
    if not update.message:
        return

    chat = update.effective_chat
    chat_id = chat.id
    user_msg_id = update.message.message_id

    # попытаемся удалить сообщение пользователя (если права позволяют)
    try:
        await try_delete_message(context.bot, chat_id, user_msg_id)
    except Exception:
        logger.debug("Не удалось удалить пользовательское сообщение /cactus")

    try:
        cactus = await db_get_cactus()
    except Exception:
        logger.exception("Ошибка при получении записи кактуса из БД")
        await context.bot.send_message(chat_id=chat_id, text="Ошибка при доступе к БД.")
        return

    if not cactus:
        msg = await context.bot.send_message(chat_id=chat_id, text="На кактусе пока нет записей.")
    else:
        dt = None
        if getattr(cactus, "updated_at", None):
            dt = cactus.updated_at.astimezone(APP_TZ)
        elif getattr(cactus, "created_at", None):
            dt = cactus.created_at.astimezone(APP_TZ)
        else:
            dt = datetime.now(APP_TZ)
        text = f"На кактусе {cactus.money}р. {dt.strftime('%d.%m.%Y %H:%M')}"
        msg = await context.bot.send_message(chat_id=chat_id, text=text)

    # удаляем сообщение через 60 секунд
    try:
        asyncio.create_task(schedule_delete(context.bot, chat_id, msg.message_id, 60))
    except Exception:
        logger.debug("Failed to schedule cactus message deletion")

async def cactusnew_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /cactusnew <sum> - только в личке.
    Обновляет (или создаёт) запись кактуса: money = <sum>, updated_at = now.
    """
    if update.effective_chat.type != "private":
        await update.message.reply_text("Команду /cactusnew можно вызывать только в личных сообщениях боту.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text("Использование: /cactusnew <сумма>\nПример: /cactusnew 25")
        return

    try:
        # допускаем целые или дробные, но сохраним как int
        parsed = float(args[0].replace(",", "."))
        money = int(parsed)
    except Exception:
        await update.message.reply_text("Неверный формат суммы. Введите число, например: /cactusnew 25")
        return

    try:
        cactus = await db_set_cactus(money)
    except Exception:
        logger.exception("Ошибка при обновлении записи кактуса в БД")
        await update.message.reply_text("Ошибка при сохранении в БД.")
        return

    dt = cactus.updated_at.astimezone(APP_TZ) if getattr(cactus, "updated_at", None) else (cactus.created_at.astimezone(APP_TZ) if getattr(cactus, "created_at", None) else datetime.now(APP_TZ))
    await update.message.reply_text(f"Значение обновлено: {cactus.money}р. {dt.strftime('%d.%m.%Y %H:%M')}")

# -------------------- Main --------------------
def main():
    # Use ApplicationBuilder to avoid NameError
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Health test
    application.add_handler(CommandHandler("ping", ping_command, filters=filters.ChatType.PRIVATE))

    # Channel posts handler
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

    # Conversation handler
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            STATE_CHOOSE_DATE: [CallbackQueryHandler(callback_calendar, pattern=r"^(CAL_PREV#|CAL_NEXT#|DAY#|IGNORE|CANCEL).*$")],
            STATE_INPUT_TIME: [MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, input_time_handler)],
            STATE_INPUT_TEXT: [MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, input_text_handler), CommandHandler("cancel", cancel_handler)],
            STATE_CONFIRM: [CallbackQueryHandler(callback_confirm_save, pattern=r"^(CONFIRM_SAVE|CANCEL)$")],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        per_user=True,
        allow_reentry=True,
        conversation_timeout=60*30
    )
    application.add_handler(conv)

    # upcoming
    application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))

    # cactus commands
    application.add_handler(CommandHandler("cactus", cactus_command))  # доступно в группах/личке
    application.add_handler(CommandHandler("cactusnew", cactusnew_command, filters=filters.ChatType.PRIVATE))  # только в личке

    # job queue
    if application.job_queue is None:
        logger.warning("JobQueue is not available. Ensure python-telegram-bot[job-queue] is installed.")
    else:
        application.job_queue.run_repeating(send_reminders_job, interval=60, first=10)

    # choose mode
    if WEBHOOK_URL and WEBHOOK_SECRET:
        logger.info("Starting webhook mode...")
        application.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path="telegram",
            webhook_url=WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "edited_message", "channel_post", "edited_channel_post", "callback_query", "my_chat_member", "chat_member"]
        )
    else:
        logger.info("WEBHOOK not configured — falling back to long polling.")
        application.run_polling(allowed_updates=["message", "edited_message", "channel_post", "edited_channel_post", "callback_query", "my_chat_member", "chat_member"])

if __name__ == "__main__":
    main()
