import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import os

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    await update.message.reply_text(
        f"Привет {user.mention_html()}! Я бот для заметок. Используй /help для списка команд.",
        parse_mode="HTML",
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = """
<b>Доступные команды:</b>
/start - Начать работу с ботом
/help - Показать это сообщение
/debug - Тестовая команда для проверки работы
"""
    await update.message.reply_text(help_text, parse_mode="HTML")

async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая команда для проверки работы бота"""
    logger.info(f"Получена команда debug от {update.effective_user.id}")
    await update.message.reply_text("✅ Бот работает корректно!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик обычных сообщений"""
    if update.message:
        logger.info(f"Получено сообщение: {update.message.text}")
        await update.message.reply_text("Сообщение получено")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    logger.error(f"Ошибка: {context.error}", exc_info=True)

def main():
    # Получаем токен бота из переменных окружения
    BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("Не указан TELEGRAM_BOT_TOKEN в переменных окружения")

    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()

    # Добавляем обработчики команд (важен порядок!)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("debug", debug))

    # Обработчик обычных сообщений (исключая команды)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    # Обработчик ошибок
    application.add_error_handler(error_handler)

    # Проверяем режим запуска
    if os.environ.get("USE_WEBHOOK", "false").lower() == "true":
        # Настройки вебхука
        WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
        PORT = int(os.environ.get("PORT", 10000))
        
        if not WEBHOOK_URL:
            raise ValueError("Не указан WEBHOOK_URL в переменных окружения")

        logger.info(f"Запуск в режиме вебхука на порту {PORT}")
        logger.info(f"URL вебхука: {WEBHOOK_URL}")

        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path="/telegram",
            webhook_url=WEBHOOK_URL,
        )
    else:
        logger.info("Запуск в режиме polling")
        application.run_polling()

if __name__ == "__main__":
    main()