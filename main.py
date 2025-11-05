import os
import re
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from database import init_db, add_note, get_upcoming_reminders_window, mark_reminder_sent, Note

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logging.getLogger('apscheduler').setLevel(logging.WARNING) # Убираем лишние логи от планировщика
logger = logging.getLogger(__name__)

# --- Загрузка конфигурации ---
load_dotenv() # Загружаем .env файл (будет нужен на сервере)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
TZ_NAME = os.environ.get("TZ", "Europe/Moscow") # Часовой пояс
APP_TZ = ZoneInfo(TZ_NAME)

if not BOT_TOKEN:
    raise ValueError("Не задан TELEGRAM_BOT_TOKEN")
if not DATABASE_URL:
    raise ValueError("Не задан DATABASE_URL")

# --- Инициализация БД ---
try:
    init_db()
    logger.info("Database initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize database: {e}")
    # В реальном приложении здесь можно было бы выйти, но мы попробуем продолжить
    # exit(1)

# --- Функции парсинга и хендлеры ---

def parse_reminder(text: str):
    """Парсит текст, ищет #напоминание и дату @HH:MM DD-MM-YYYY"""
    hashtags = re.findall(r"#[а-яА-ЯёЁa-zA-Z0-9_]+", text)
    dt_match = re.search(r"@(\d{2}:\d{2}) (\d{2}-\d{2}-\d{4})", text)
    reminder_date = None
    
    if dt_match:
        time_str, date_str = dt_match.groups()
        try:
            # Парсим "локальное" время
            naive_dt = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
            # Привязываем к часовому поясу приложения
            reminder_date = naive_dt.replace(tzinfo=APP_TZ)
            logger.info(f"Parsed date: {reminder_date}")
        except ValueError:
            logger.warning(f"Invalid date format: {dt_match.group(0)}")
            return text, " ".join(hashtags), None
            
    # Убираем из текста теги и дату
    cleaned_text = re.sub(r"#[а-яА-ЯёЁa-zA-Z0-9_]+", "", text).strip()
    if dt_match:
        cleaned_text = cleaned_text.replace(dt_match.group(0), "").strip()
        
    return cleaned_text, hashtags, reminder_date

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик постов в канале"""
    if not update.channel_post or not update.channel_post.text:
        return
        
    text = update.channel_post.text
    channel_id = update.channel_post.chat.id
    
    cleaned_text, hashtags, reminder_date = parse_reminder(text)
    
    if "#напоминание" not in hashtags or reminder_date is None:
        logger.info("Ignoring post: no #напоминание or valid date found.")
        return
        
    try:
        # Важно: В базу данных дата должна уходить в UTC
        reminder_date_utc = reminder_date.astimezone(ZoneInfo("UTC"))
        
        note = add_note(channel_id, cleaned_text, " ".join(hashtags), reminder_date_utc)
        
        # Для ответа пользователю снова конвертируем в его зону
        reply_date_str = reminder_date.strftime('%H:%M %d-%m-%Y')
        reply = f"✅ Напоминание сохранено: «{note.text}» на {reply_date_str}"
        await update.channel_post.reply_text(reply)
        logger.info(f"Saved reminder from channel {channel_id}: {note.text}")
        
    except Exception as e:
        logger.error(f"Error saving note from channel: {e}")
        await update.channel_post.reply_text(f"❌ Ошибка сохранения: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    await update.message.reply_text("Привет! Я бот для напоминаний. Используйте /upcoming для просмотра предстоящих напоминаний.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    help_text = """
    Доступные команды:
    /start - Приветствие
    /help - Помощь
    /upcoming - Показать предстоящие напоминания
    
    Бот отслеживает посты в канале с форматом:
    Текст #напоминание @HH:MM DD-MM-YYYY
    """
    await update.message.reply_text(help_text)

async def upcoming_notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /upcoming"""
    now_utc = datetime.now(ZoneInfo("UTC"))
    end_of_time = now_utc + timedelta(days=365*10) # Смотрим далеко вперед
    
    try:
        notes = get_upcoming_reminders_window(now_utc, end_of_time, only_unsent=True)
        
        if not notes:
            await update.message.reply_text("Нет предстоящих неотправленных напоминаний.")
            return
            
        messages = ["🔔 Предстоящие напоминания:"]
        for note in notes:
            # note.reminder_date должен быть в UTC
            reminder_date_local = note.reminder_date.astimezone(APP_TZ)
            messages.append(
                f"• «{note.text}» - {reminder_date_local.strftime('%H:%M %d-%m-%Y')}"
            )
        await update.message.reply_text("\n".join(messages[:15])) # Ограничим вывод
        
    except Exception as e:
        logger.error(f"Error fetching upcoming notes: {e}")
        await update.message.reply_text(f"❌ Ошибка получения напоминаний: {e}")

# --- Задача для Планировщика ---

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """
    Проверяет БД на наличие напоминаний, которые нужно отправить.
    Вызывается планировщиком.
    """
    # Ищем напоминания, которые должны сработать в ближайшие 24 часа
    # (по вашему изначальному ТЗ)
    now_utc = datetime.now(ZoneInfo("UTC"))
    window_end_utc = now_utc + timedelta(days=1)
    
    logger.info(f"Checking reminders... Window: {now_utc} to {window_end_utc}")
    
    try:
        upcoming = get_upcoming_reminders_window(now_utc, window_end_utc, only_unsent=True)
        if not upcoming:
            logger.info("No reminders to send in this window.")
            return

        for note in upcoming:
            try:
                # Конвертируем UTC из базы в локальное время для отображения
                reminder_date_local = note.reminder_date.astimezone(APP_TZ)
                
                await context.bot.send_message(
                    chat_id=note.user_id, # user_id это ID канала (или юзера)
                    text=f"🔔 Напоминание: «{note.text}» назначено на {reminder_date_local.strftime('%H:%M %d-%m-%Y')}"
                )
                mark_reminder_sent(note.id)
                logger.info(f"Sent reminder {note.id} to {note.user_id}")
                
            except Exception as e:
                logger.error(f"Failed to send reminder {note.id}: {e}")
                
    except Exception as e:
        logger.error(f"Critical error in check_reminders job: {e}")

# --- Запуск Бота ---

def main():
    logger.info("Starting bot...")
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавляем JobQueue (встроенный в PTB планировщик, лучше APScheduler для PTB)
    # Вместо APScheduler, используем встроенный JobQueue. Это проще и надежнее.
    job_queue = application.job_queue
    
    # Запускаем `check_reminders` каждые 60 секунд. 
    # `first=10` значит, что первая проверка будет через 10 секунд после старта.
    job_queue.run_repeating(check_reminders, interval=60, first=10)
    
    # Хендлер для постов в канале
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.CHANNEL, 
        handle_channel_post
    ))

    # Команды (ограничены приватными чатами)
    application.add_handler(CommandHandler("start", start_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("help", help_command, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("upcoming", upcoming_notes_command, filters=filters.ChatType.PRIVATE))

    # Запускаем бота в режиме polling (постоянное подключение)
    logger.info("Starting polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()