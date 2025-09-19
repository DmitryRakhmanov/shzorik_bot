import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Text, select, func
)
from sqlalchemy.orm import declarative_base, sessionmaker

# --- Настройка базы данных ---
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///notes.db")  # для локального теста
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)
Base = declarative_base()

# --- Модель заметки ---
class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)  # None для каналов
    text = Column(Text, nullable=False)
    hashtags = Column(String, nullable=True)
    reminder_date = Column(DateTime(timezone=True), nullable=True)

# --- Инициализация базы данных ---
def initialize_db():
    Base.metadata.create_all(bind=engine)

# --- CRUD операции ---
def add_note(user_id, text, hashtags=None, reminder_date=None, tz_str="Europe/Moscow"):
    """Добавляем заметку с timezone-aware datetime."""
    if reminder_date:
        if reminder_date.tzinfo is None:
            tz = ZoneInfo(tz_str)
            reminder_date = reminder_date.replace(tzinfo=tz)
    note = Note(user_id=user_id, text=text, hashtags=hashtags, reminder_date=reminder_date)
    with SessionLocal() as session:
        session.add(note)
        session.commit()

def find_notes_by_user_and_hashtag(user_id, hashtag):
    with SessionLocal() as session:
        stmt = select(Note).where(
            Note.user_id == user_id,
            Note.hashtags.ilike(f"%{hashtag}%")
        ).order_by(Note.id.desc())
        return session.scalars(stmt).all()

def get_all_notes_for_user(user_id):
    with SessionLocal() as session:
        stmt = select(Note).where(Note.user_id == user_id).order_by(Note.id.desc())
        return session.scalars(stmt).all()

def update_note_reminder_date(note_id, new_date=None):
    """Сбрасываем или обновляем дату напоминания."""
    with SessionLocal() as session:
        note = session.get(Note, note_id)
        if note:
            note.reminder_date = new_date
            session.commit()

# --- Новая функция для получения предстоящих напоминаний ---
def get_upcoming_reminders_window(hours_before=24, tz_str="Europe/Moscow"):
    """
    Возвращает список заметок, у которых reminder_date в пределах
    следующих hours_before часов от текущего времени.
    """
    now = datetime.now(tz=ZoneInfo(tz_str))
    window_end = now + timedelta(hours=hours_before)
    with SessionLocal() as session:
        stmt = select(Note).where(
            Note.reminder_date != None,
            Note.reminder_date >= now,
            Note.reminder_date <= window_end
        ).order_by(Note.reminder_date)
        return session.scalars(stmt).all()

# --- Привычная для main.py функция ---
def get_upcoming_reminders():
    """Возвращает все напоминания за следующие 24 часа (timezone-aware)."""
    return get_upcoming_reminders_window(hours_before=24)
