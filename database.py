import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base # Updated import for declarative_base

# Получаем URL базы данных из переменной окружения Render.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable is not set!")

# Создаем движок базы данных с настройками пула соединений.
# Эти настройки помогают предотвратить неожиданные разрывы соединений.
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_recycle=300, # Recycle connections after 5 minutes (300 seconds)
    pool_pre_ping=True, # Test connections before use
    echo=False # Set to True for debugging SQL queries
)

Base = declarative_base()

class Note(Base):
    """
    Представляет заметку в базе данных.
    Включает поля для ID пользователя, текста заметки, хэштегов и даты напоминания.
    """
    __tablename__ = 'notes'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True) # CHANGED TO nullable=True for flexibility
    text = Column(Text, nullable=False)
    hashtags = Column(String) # Хранится как строка хэштегов, разделенных пробелами
    reminder_date = Column(DateTime) # Дата и время события, о котором нужно напомнить

    def __repr__(self):
        return f"<Note(id={self.id}, user_id={self.user_id}, text='{self.text[:20]}...')>"

# Создаем фабрику сессий, привязанную к нашему настроенному движку.
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Инициализация базы данных (создание таблиц)
# Эта функция вызывается один раз при запуске приложения.
def initialize_db():
    Base.metadata.create_all(bind=engine)

# --- Функции CRUD для заметок ---

def add_note(user_id: int, text: str, hashtags: str = None, reminder_date: datetime.datetime = None):
    """
    Добавляет новую заметку в базу данных.
    """
    session = SessionLocal() # Use SessionLocal from sessionmaker
    try:
        new_note = Note(user_id=user_id, text=text, hashtags=hashtags, reminder_date=reminder_date)
        session.add(new_note)
        session.commit()
        session.refresh(new_note) # Refresh to get auto-generated ID if needed
        return new_note
    except Exception as e:
        session.rollback() # Откатываем изменения в случае ошибки
        raise e
    finally:
        session.close() # Всегда закрываем сессию, чтобы освободить соединение

def update_note_reminder_date(note_id: int):
    """
    Устанавливает reminder_date для конкретной заметки в None,
    тем самым помечая ее как обработанную, чтобы напоминание не отправлялось повторно.
    """
    session = SessionLocal()
    try:
        note = session.query(Note).filter(Note.id == note_id).first()
        if note:
            note.reminder_date = None  # Обнуляем дату напоминания
            session.commit()
            return True
        return False
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

def find_notes_by_user_and_hashtag(user_id: int, hashtag: str):
    """
    Находит заметки для конкретного пользователя по заданному хэштегу (без учета регистра).
    Учитывает, что заметки из канала могут не иметь user_id.
    """
    session = SessionLocal()
    try:
        # Ищем заметки, где user_id совпадает ИЛИ user_id = None (для канала),
        # а хэштеги содержат искомый хэштег.
        # Note: ILIKE is PostgreSQL specific for case-insensitive LIKE
        # For SQLite/MySQL you might need to use lower() and LIKE
        notes = session.query(Note).filter(
            (Note.user_id == user_id) | (Note.user_id.is_(None)), # Allows notes from private chat AND channel
            Note.hashtags.ilike(f'%{hashtag}%') 
        ).all()
        return notes
    finally:
        session.close()

def get_upcoming_reminders():
    """
    Извлекает напоминания, которые либо уже наступили, либо наступят в течение следующих 24 часов
    и для которых напоминание еще не отправлено (reminder_date не None).
    """
    session = SessionLocal()
    try:
        now = datetime.datetime.now()
        reminders = session.query(Note).filter(
            Note.reminder_date.isnot(None), # Reminder date must exist
            Note.reminder_date <= now + datetime.timedelta(days=1) # Due now or within next 24 hours
        ).order_by(Note.reminder_date).all()
        return reminders
    finally:
        session.close()

def get_all_notes_for_user(user_id: int):
    """
    Извлекает все заметки, хранящиеся для конкретного пользователя.
    """
    session = SessionLocal()
    try:
        notes = session.query(Note).filter(Note.user_id == user_id).all()
        return notes
    finally:
        session.close()