# database.py
import os
import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base

# Получите DATABASE_URL из переменных окружения
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable is not set!")

# Настройка движка SQLAlchemy
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_recycle=300,
    pool_pre_ping=True,
    echo=False,
)

Base = declarative_base()

class Note(Base):
    __tablename__ = 'notes'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True)
    text = Column(Text, nullable=False)
    hashtags = Column(String)
    # Храним timezone-aware datetime в UTC
    reminder_date = Column(DateTime(timezone=True))

    def __repr__(self):
        return f"<Note(id={self.id}, user_id={self.user_id}, text='{self.text[:20]}...')>"

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Таймзоны
LOCAL_TZ = ZoneInfo('Europe/Amsterdam')
UTC = ZoneInfo('UTC')

def initialize_db():
    """Создаёт таблицы (если ещё нет)."""
    Base.metadata.create_all(bind=engine)

# --- CRUD функции ---

def add_note(user_id: int, text: str, hashtags: str = None, reminder_date: datetime.datetime = None):
    """
    Добавляет заметку. Если reminder_date не-таймзонный (naive),
    считается, что это время в Europe/Amsterdam и переводится в UTC перед сохранением.
    """
    session = SessionLocal()
    try:
        if reminder_date is not None:
            if reminder_date.tzinfo is None:
                # считаем, что naive время — в локальной TZ (Europe/Amsterdam)
                reminder_date = reminder_date.replace(tzinfo=LOCAL_TZ)
            # конвертируем в UTC для хранения
            reminder_date = reminder_date.astimezone(UTC)

        new_note = Note(user_id=user_id, text=text, hashtags=hashtags, reminder_date=reminder_date)
        session.add(new_note)
        session.commit()
        session.refresh(new_note)
        return new_note
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def update_note_reminder_date(note_id: int):
    """
    Устанавливает reminder_date = NULL (None) у заметки по id.
    Возвращает True если обновлено, False если заметка не найдена.
    """
    session = SessionLocal()
    try:
        note = session.query(Note).filter(Note.id == note_id).first()
        if note:
            note.reminder_date = None
            session.commit()
            return True
        return False
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

def find_notes_by_user_and_hashtag(user_id: int, hashtag: str):
    """
    Находит заметки для пользователя или из канала (user_id IS NULL), где hashtags содержит hashtag.
    Регистр игнорируется (ILIKE для Postgres).
    """
    session = SessionLocal()
    try:
        notes = session.query(Note).filter(
            (Note.user_id == user_id) | (Note.user_id.is_(None)),
            Note.hashtags.ilike(f'%{hashtag}%')
        ).all()
        return notes
    finally:
        session.close()

def get_upcoming_reminders_window(window_start: datetime.datetime, window_end: datetime.datetime):
    """
    Возвращает список Note с reminder_date в диапазоне [window_start, window_end].
    Ожидает timezone-aware datetime или naive (будет приведён к UTC).
    Возвращаемые объекты ORM — годны для чтения; для обновления используйте update_note_reminder_date().
    """
    session = SessionLocal()
    try:
        # Нормализуем границы: приводим к UTC и делаем aware, если нужно
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=UTC)
        else:
            window_start = window_start.astimezone(UTC)

        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=UTC)
        else:
            window_end = window_end.astimezone(UTC)

        reminders = session.query(Note).filter(
            Note.reminder_date.isnot(None),
            Note.reminder_date >= window_start,
            Note.reminder_date <= window_end
        ).order_by(Note.reminder_date).all()
        return reminders
    finally:
        session.close()

def get_all_notes_for_user(user_id: int):
    session = SessionLocal()
    try:
        notes = session.query(Note).filter(Note.user_id == user_id).all()
        return notes
    finally:
        session.close()
