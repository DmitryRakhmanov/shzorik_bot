import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from database import init_db
from flask import Flask, jsonify

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения из .env
load_dotenv()

# Инициализация базы данных
init_db()
logger.info("Database initialized.")

# Flask приложение для пинга
app = Flask(__name__)

@app.route('/')
def home():
    return "Reminder Bot API is running! Reminders are handled by GitHub Actions."

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({"status": "OK", "timestamp": datetime.now().isoformat()}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)