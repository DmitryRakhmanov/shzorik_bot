import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Получаем URL базы данных из переменной окружения
# Это очень важно для Render.com, так как мы не будем хардкодить URL в коде
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable is not set!")

# --- Создаем движок базы данных с настройками пула соединений ---
# Используем DATABASE_URL, полученный из переменных окружения.
# Параметры пула соединений помогут решить проблему с обрывами SSL-соединений:
# pool_size: Максимальное количество соединений, которые будут поддерживаться в пуле.
# max_overflow: Дополнительное количество соединений, которые могут быть временно открыты
#               сверх pool_size, если все соединения в пуле заняты.
# pool_recycle: Время в секундах, по истечении которого соединение будет "перезапускаться"
#               (проверяться/пересоздаваться), чтобы избежать таймаутов на стороне БД.
#               Значение 300 секунд (5 минут) часто хорошо работает, т.к. многие БД имеют
#               таймаут неактивности 5-10 минут.
# pool_pre_ping: Если True, SQLAlchemy отправит "легкий" запрос к БД перед использованием
#                соединения из пула, чтобы убедиться, что оно еще активно.
# echo=False: Отключаем подробное логирование SQL-запросов в консоль (можно поставить True для дебага).
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_recycle=300, # Перезапускаем соединения каждые 5 минут (300 секунд)
    pool_pre_ping=True, # Проверяем соединение перед использованием
    echo=False
)

Base = declarative_base()

class Note(Base):
    __tablename__ = 'notes'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    hashtags = Column(String) # Хранится как строка с тегами, разделёнными пробелами
    reminder_date = Column(DateTime)

    def __repr__(self):
        return f"<Note(id={self.id}, user_id={self.user_id}, text='{self.text[:20]}...')>"

# Создаем таблицы в базе данных, если их нет
# Это должно быть вызвано один раз при инициализации приложения
Base.metadata.create_all(engine)

# Создаем фабрику сессий, которая будет использовать наш сконфигурированный движок
Session = sessionmaker(bind=engine)

def add_note(user_id: int, text: str, hashtags: str = None, reminder_date: datetime.datetime = None):
    """Добавляет новую заметку в базу данных."""
    session = Session()
    try:
        new_note = Note(user_id=user_id, text=text, hashtags=hashtags, reminder_date=reminder_date)
        session.add(new_note)
        session.commit()
        # session.refresh(new_note) # Обычно используется, если нужны сгенерированные БД значения (вроде ID)
        return new_note
    except Exception as e:
        session.rollback() # Откатываем изменения при ошибке
        raise e
    finally:
        session.close() # Всегда закрываем сессию

def find_notes_by_user_and_hashtag(user_id: int, hashtag: str):
    """Ищет заметки пользователя по хештегу (регистронезависимо)."""
    session = Session()
    # Используем ILIKE для регистронезависимого поиска подстроки
    notes = session.query(Note).filter(
        Note.user_id == user_id,
        Note.hashtags.ilike(f'%{hashtag}%')
    ).all()
    session.close()
    return notes

def get_upcoming_reminders():
    """Возвращает напоминания, которые должны сработать в течение следующих 24 часов."""
    session = Session()
    now = datetime.datetime.now()
    reminders = session.query(Note).filter(
        Note.reminder_date.isnot(None),
        Note.reminder_date > now, # Напоминания в будущем
        Note.reminder_date <= now + datetime.timedelta(days=1) # Напоминания в течение ближайших 24 часов
    ).all()
    session.close()
    return reminders

def get_all_notes_for_user(user_id: int):
    """Возвращает все заметки для указанного пользователя."""
    session = Session()
    notes = session.query(Note).filter(Note.user_id == user_id).all()
    session.close()
    return notes