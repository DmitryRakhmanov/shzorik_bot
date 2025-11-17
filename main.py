# main.py
import os
import re
import logging
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

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

# предполагается, что в database.py есть: init_db(), add_note(channel_id, text, hashtags, remind_utc), get_upcoming_reminders_window(...)
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
except Exception as e:
    logger.error(f"DB init failed: {e}")
    raise

# -------------------- Conversation states --------------------
(
    STATE_CHOOSE_DATE,    # пользователь выбирает день в календаре (callback_query)
    STATE_CHOOSE_HOUR,    # выбирает час (callback_query)
    STATE_CHOOSE_MIN,     # выбирает минуту (callback_query)
    STATE_INPUT_TEXT,     # вводит текст напоминания (message)
    STATE_CONFIRM,        # подтверждение (callback_query)
) = range(5)

# -------------------- Утилиты --------------------
def parse_hashtags(text: str):
    return " ".join(re.findall(r"#[\wа-яА-ЯёЁ]+", text))

def build_calendar(year: int, month: int):
    """Возвращает InlineKeyboardMarkup простого календаря для month/year."""
    import calendar
    cal = calendar.Calendar(firstweekday=0)
    keyboard = []

    # header: месяц/год + навигация
    keyboard.append([
        InlineKeyboardButton("<", callback_data=f"CAL_PREV#{year}#{month}"),
        InlineKeyboardButton(f"{calendar.month_name[month]} {year}", callback_data="IGNORE"),
        InlineKeyboardButton(">", callback_data=f"CAL_NEXT#{year}#{month}")
    ])

    # weekdays
    week_days = ["Mo","Tu","We","Th","Fr","Sa","Su"]
    keyboard.append([InlineKeyboardButton(w, callback_data="IGNORE") for w in week_days])

    # days
    month_days = cal.monthdayscalendar(year, month)
    for week in month_days:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(" ", callback_data="IGNORE"))
            else:
                row.append(InlineKeyboardButton(str(day), callback_data=f"DAY#{year}#{month}#{day}"))
        keyboard.append(row)

    # Конец / отмена
    keyboard.append([
        InlineKeyboardButton("Отмена", callback_data="CANCEL")
    ])

    return InlineKeyboardMarkup(keyboard)

def build_hours_keyboard():
    """Клавиатура выбора часа (0-23), строки по 6 кнопок."""
    keyboard = []
    row = []
    for h in range(24):
        row.append(InlineKeyboardButton(f"{h:02d}", callback_data=f"HOUR#{h}"))
        if len(row) == 6:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("Назад", callback_data="BACK_TO_CAL"), InlineKeyboardButton("Отмена", callback_data="CANCEL")])
    return InlineKeyboardMarkup(keyboard)

def build_minutes_keyboard():
    """Клавиатура выбора минут с шагом 5."""
    keyboard = []
    row = []
    for m in range(0, 60, 5):
        row.append(InlineKeyboardButton(f"{m:02d}", callback_data=f"MIN#{m}"))
        if len(row) == 6:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("Назад (выбрать час)", callback_data="BACK_TO_HOUR"), InlineKeyboardButton("Отмена", callback_data="CANCEL")])
    return InlineKeyboardMarkup(keyboard)

async def send_and_track(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, reply_markup=None):
    """
    Отправить сообщение и вернуть message объект; также сохраняет id в context.user_data['msg_ids'] чтобы потом удалить.
    """
    msg = await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    user_data = context.user_data
    user_data.setdefault("msg_ids", []).append(msg.message_id)
    return msg

async def cleanup_messages(context: ContextTypes.DEFAULT_TYPE):
    """Удалить все промежуточные сообщения, кроме final_message_id (если указан)."""
    user_data = context.user_data
    chat_id = user_data.get("dialog_chat_id")
    if not chat_id:
        return
    keep = user_data.get("final_message_id")
    ids = user_data.get("msg_ids", [])
    for mid in ids:
        try:
            if keep and mid == keep:
                continue
            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            # игнорируем ошибки удаления
            pass
    user_data["msg_ids"] = []
    return

# -------------------- Handlers --------------------

# 1) Когда в канале пишет кто-то "/notify" — бот публикует кнопку с deep link в ЛС
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.channel_post or not update.channel_post.text:
        return

    text = update.channel_post.text.strip()
    chat_id = update.channel_post.chat.id

    # если команда /notify в канале
    if text.startswith("/notify"):
        # получить username бота (асинхронно доступно в context.bot.username)
        bot_username = context.bot.username or os.environ.get("BOT_USERNAME")
        if not bot_username:
            # попробуем fetchMe (на всякий случай)
            try:
                me = await context.bot.get_me()
                bot_username = me.username
            except Exception:
                bot_username = None

        if not bot_username:
            await update.channel_post.reply_text("Ошибка: не могу определить username бота для deep-link. Обратитесь к администратору.")
            return

        # deep link start parameter: notify_{channel_id}
        start_param = f"notify_{chat_id}"
        deep_link = f"https://t.me/{bot_username}?start={start_param}"

        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Создать в личных сообщениях", url=deep_link)]])
        await update.channel_post.reply_text(
            "Чтобы создать интерактивное напоминание, перейдите в личные сообщения бота:",
            reply_markup=kb
        )
        logger.info(f"Posted deep link for channel {chat_id} -> {deep_link}")
        return

    # иначе — можно обрабатывать старый формат напоминаний (#напоминание ... @HH:MM DD-MM-YYYY)
    # оставим вашу старую логику (упрощённый вариант)
    #Парсим #напоминание
    hashtags = re.findall(r"#[\wа-яА-ЯёЁ]+", text)
    dt_match = re.search(r"@(\d{2}:\d{2}) (\d{2}-\d{2}-\d{4})", text)
    if "#напоминание" not in hashtags or not dt_match:
        logger.info("Channel post ignored (no #напоминание or no date).")
        return

    # оригинальная обработка (сохранение в БД)
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
        add_note(chat_id, f"{cleaned_text} (событие: {event_date.strftime('%H:%M %d-%m-%Y')})", " ".join(hashtags), remind_utc)
        await update.channel_post.reply_text(f"✅ Напоминание сохранено. Уведомление: {remind_at.strftime('%H:%M %d-%m-%Y')}")
        logger.info(f"Saved channel reminder: {cleaned_text}")
    except Exception as e:
        logger.exception("Error saving channel reminder")
        await update.channel_post.reply_text(f"Ошибка при сохранении напоминания: {e}")


# 2) /start — при deep-link начинаем диалог
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start [payload] — если payload начинается с notify_{channel_id}, запускаем диалог.
    Иначе обычное приветствие.
    """
    args = context.args or []
    payload = args[0] if args else None
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # сохраняем id чата куда в итоге нужно отправлять (канал)
    if payload and payload.startswith("notify_"):
        try:
            channel_id = int(payload.split("_", 1)[1])
        except Exception:
            await update.message.reply_text("Неверный параметр запуска.")
            return
        # пометим в user_data куда сохраняем напоминание
        context.user_data.clear()
        context.user_data["target_channel_id"] = channel_id
        context.user_data["dialog_chat_id"] = chat_id  # где ведём диалог (личка)
        context.user_data["msg_ids"] = []

        # отправляем календарь (текущий месяц)
        today = date.today()
        calendar_markup = build_calendar(today.year, today.month)
        msg = await send_and_track(context, chat_id, "Выберите дату события (календарь):", reply_markup=calendar_markup)
        # состояние — выбор даты
        return STATE_CHOOSE_DATE

    # иначе простое приветствие
    await update.message.reply_text("Привет! Я бот-напоминалка. Используйте /upcoming для списка напоминаний или нажмите /start notify_<channel_id>.")

# 3) CallbackQuery: календарь навигация или выбор дня
async def callback_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "IGNORE":
        return

    if data == "CANCEL":
        # пользователь отменил — очистим и завершить диалог
        await query.edit_message_text("Диалог отменён.")
        await cleanup_messages(context)
        return ConversationHandler.END

    # навигация месяцев
    if data.startswith("CAL_PREV#") or data.startswith("CAL_NEXT#"):
        parts = data.split("#")
        cmd, year_str, month_str = parts
        year, month = int(year_str), int(month_str)
        if data.startswith("CAL_PREV#"):
            # перейти на предыдущ месяц
            if month == 1:
                month = 12
                year -= 1
            else:
                month -= 1
        else:
            # next
            if month == 12:
                month = 1
                year += 1
            else:
                month += 1
        await query.edit_message_text("Выберите дату события (навигация):", reply_markup=build_calendar(year, month))
        return STATE_CHOOSE_DATE

    # выбран день
    if data.startswith("DAY#"):
        _, year_str, month_str, day_str = data.split("#")
        year, month, day = int(year_str), int(month_str), int(day_str)
        # запомним выбранную дату в user_data
        context.user_data["event_date"] = date(year, month, day)
        # переходим к выбору часа
        await query.edit_message_text(f"Вы выбрали: {day:02d}-{month:02d}-{year}. Теперь выберите час:", reply_markup=build_hours_keyboard())
        return STATE_CHOOSE_HOUR

    # неизвестный callback — игнор
    return

# 4) CallbackQuery: выбирать час, мин
async def callback_hour_or_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "CANCEL":
        await query.edit_message_text("Диалог отменён.")
        await cleanup_messages(context)
        return ConversationHandler.END

    if data == "BACK_TO_CAL":
        # Вернуться к календарю (используем текущий месяц выбранной event_date или сегодняшний)
        ev_date = context.user_data.get("event_date") or date.today()
        await query.edit_message_text("Выберите дату события (календарь):", reply_markup=build_calendar(ev_date.year, ev_date.month))
        return STATE_CHOOSE_DATE

    if data == "BACK_TO_HOUR":
        # назад к выбору часа
        await query.edit_message_text("Выберите час:", reply_markup=build_hours_keyboard())
        return STATE_CHOOSE_HOUR

    if data.startswith("HOUR#"):
        _, hour_str = data.split("#")
        hour = int(hour_str)
        context.user_data["event_hour"] = hour
        # переходим к минутам
        await query.edit_message_text(f"Выбран час: {hour:02d}. Теперь выберите минуты:", reply_markup=build_minutes_keyboard())
        return STATE_CHOOSE_MIN

    if data.startswith("MIN#"):
        _, min_str = data.split("#")
        minute = int(min_str)
        context.user_data["event_minute"] = minute

        # Соберём дату и время и проверим, что событие минимум через 24 часа
        ev_date = context.user_data.get("event_date")
        hour = context.user_data.get("event_hour")
        minute = context.user_data.get("event_minute")
        if ev_date is None or hour is None or minute is None:
            await query.edit_message_text("Ошибка: дата/время не заданы. Попробуйте ещё раз.")
            return ConversationHandler.END

        dt = datetime(ev_date.year, ev_date.month, ev_date.day, hour, minute, tzinfo=APP_TZ)
        now = datetime.now(APP_TZ)
        if dt < now + timedelta(days=1):
            # событие слишком близко
            await query.edit_message_text("❌ Дата/время должны быть не ранее, чем через 24 часа. Выберите новое время.")
            # возврат к календарю
            await query.edit_message_text("Выберите дату события (календарь):", reply_markup=build_calendar(ev_date.year, ev_date.month))
            return STATE_CHOOSE_DATE

        # готово — попросить ввести текст события
        await query.edit_message_text(
            f"Вы выбрали событие: {dt.strftime('%H:%M %d-%m-%Y')}.\n"
            "Теперь введите текст напоминания (одно сообщение).\n\n"
            "После ввода вы увидите экран подтверждения."
        )
        return STATE_INPUT_TEXT

    # прочее — игнор
    return

# 5) Message: ввод текста напоминания
async def input_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    # сохраняем текст
    context.user_data["event_text"] = text.strip()
    ev_date = context.user_data.get("event_date")
    hour = context.user_data.get("event_hour")
    minute = context.user_data.get("event_minute")
    if not (ev_date and hour is not None and minute is not None):
        await update.message.reply_text("Ошибка: дата/время отсутствуют. Запустите диалог заново.")
        return ConversationHandler.END

    dt = datetime(ev_date.year, ev_date.month, ev_date.day, hour, minute, tzinfo=APP_TZ)
    # Составляем превью и кнопки подтверждения
    preview = (
        f"Проверьте напоминание:\n\n"
        f"Текст: {context.user_data['event_text']}\n"
        f"Когда: {dt.strftime('%H:%M %d-%m-%Y')}\n"
        f"Куда: канал (id {context.user_data.get('target_channel_id')})\n\n"
        f"Нажмите Подтвердить — чтобы сохранить. Или Отмена."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Подтвердить ✅", callback_data="CONFIRM_SAVE"), InlineKeyboardButton("Отмена ❌", callback_data="CANCEL")]
    ])
    msg = await send_and_track(context, update.effective_chat.id, preview, reply_markup=kb)
    # отмечаем этот message как final пока не удалять
    context.user_data["final_message_id"] = msg.message_id
    return STATE_CONFIRM

# 6) CallbackQuery: подтверждение сохранения
async def callback_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "CANCEL":
        await query.edit_message_text("Диалог отменён.")
        await cleanup_messages(context)
        return ConversationHandler.END

    if data == "CONFIRM_SAVE":
        # собираем данные
        ev_date = context.user_data.get("event_date")
        hour = context.user_data.get("event_hour")
        minute = context.user_data.get("event_minute")
        text = context.user_data.get("event_text", "").strip()
        channel_id = context.user_data.get("target_channel_id")
        if not (ev_date and hour is not None and minute is not None and text and channel_id):
            await query.edit_message_text("Ошибка: неполные данные. Попробуйте снова.")
            await cleanup_messages(context)
            return ConversationHandler.END

        event_dt = datetime(ev_date.year, ev_date.month, ev_date.day, hour, minute, tzinfo=APP_TZ)
        remind_at = event_dt - timedelta(days=1)
        remind_utc = remind_at.astimezone(ZoneInfo("UTC"))

        # hashtags — пока пусто, т.к. создаём вручную
        hashtags = parse_hashtags(text)

        try:
            # сохранить в БД — используем add_note(channel_id, text_with_event, hashtags, remind_utc)
            text_with_event = f"{text} (событие: {event_dt.strftime('%H:%M %d-%m-%Y')})"
            note = add_note(channel_id, text_with_event, hashtags or "", remind_utc)

            # сообщим пользователю в ЛС и в канал
            await query.edit_message_text("✅ Напоминание сохранено. Финальное сообщение в личных сообщениях.")
            # Отправим подтверждение в ЛС (финальное, которое не удаляем)
            final = await context.bot.send_message(
                chat_id=context.user_data.get("dialog_chat_id"),
                text=f"✅ Напоминание создано и будет отправлено в канал <b>{channel_id}</b> за 24 часа до события ({remind_at.strftime('%H:%M %d-%m-%Y')}).",
                parse_mode="HTML"
            )
            # Отправим короткое уведомление в канал (опционально — чтобы увидеть, что новое напоминание создано)
            try:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=f"🔔 Новое напоминание создано: «{text}»\n(уведомление будет отправлено за 24 часа)"
                )
            except Exception as e:
                # возможно канал не принимает сообщения от бота напрямую (права) — логируем
                logger.warning(f"Не удалось отправить подтверждение в канал {channel_id}: {e}")

            # сохранение final message id чтобы cleanup не удалял его если надо
            context.user_data["final_message_id"] = final.message_id

        except Exception as e:
            logger.exception("Ошибка при сохранении напоминания")
            await query.edit_message_text(f"Ошибка при сохранении: {e}")
            await cleanup_messages(context)
            return ConversationHandler.END

        # после сохранения удаляем все промежуточные сообщения, кроме final
        await cleanup_messages(context)
        return ConversationHandler.END

    # прочие случаи
    return

# 7) Timeout/Cancel handler
async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Если пользователь вводит /cancel текстом."""
    await update.message.reply_text("Диалог отменён по запросу пользователя.")
    await cleanup_messages(context)
    return ConversationHandler.END

# 8) /upcoming в личке
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
    except Exception as e:
        logger.exception("Error fetching upcoming notes")
        await update.message.reply_text(f"Ошибка: {e}")

# -------------------- MAIN --------------------
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Хендлеры для channel_post: старые форматы + /notify -> deep link
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_channel_post))

    # ConversationHandler для личного диалога /notify
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            STATE_CHOOSE_DATE: [
                CallbackQueryHandler(callback_calendar, pattern=r"^(CAL_PREV|CAL_NEXT|DAY|IGNORE|CANCEL)#?.*|^IGNORE$|^CANCEL$")
            ],
            STATE_CHOOSE_HOUR: [
                CallbackQueryHandler(callback_hour_or_min, pattern=r"^(HOUR#|BACK_TO_CAL|CANCEL).+|^(BACK_TO_CAL|CANCEL)$|^HOUR#\d+$")
            ],
            STATE_CHOOSE_MIN: [
                CallbackQueryHandler(callback_hour_or_min, pattern=r"^(MIN#|BACK_TO_HOUR|CANCEL).+|^(BACK_TO_HOUR|CANCEL)$|^MIN#\d+$")
            ],
            STATE_INPUT_TEXT: [
                MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, input_text_handler),
                CommandHandler("cancel", cancel_handler)
            ],
            STATE_CONFIRM: [
                CallbackQueryHandler(callback_confirm, pattern=r"^(CONFIRM_SAVE|CANCEL)$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        allow_reentry=True,
        per_user=True,
        conversation_timeout=60*30  # 30 минут
    )

    application.add_handler(conv_handler)

    # команды в ЛС
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
