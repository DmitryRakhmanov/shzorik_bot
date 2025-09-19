# database.py
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import sessionmaker, declarative_base
import os

DATABASE_URL = os.environ.get("DATABASE_URL")  # PostgreSQL URL, например: postgres://user:pass@host:port/db

Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)
    text = Column(Text, nullable=False)
    hashtags = Column(String, nullable=True)
    reminder_date = Column(DateTime(timezone=True), nullable=True)

def initialize_db():
    Base.metadata.create_all(bind=engine)

def add_note(user_id, text, hashtags=None, reminder_date=None):
    with SessionLocal() as session:
        note = Note(
            user_id=user_id,
            text=text,
            hashtags=hashtags,
            reminder_date=reminder_date
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        return note

def find_notes_by_user_and_hashtag(user_id, hashtag):
    with SessionLocal() as session:
        notes = session.query(Note).filter(
            Note.user_id == user_id,
            Note.hashtags.ilike(f"%{hashtag}%")
        ).order_by(Note.id.desc()).all()
        return notes

def get_all_notes_for_user(user_id):
    with SessionLocal() as session:
        notes = session.query(Note).filter(Note.user_id == user_id).order_by(Note.id.desc()).all()
        return notes

def update_note_reminder_date(note_id, new_date=None):
    """Сбрасывает reminder_date после отправки напоминания или обновляет на new_date."""
    with SessionLocal() as session:
        note = session.get(Note, note_id)
        if note:
            note.reminder_date = new_date
            session.commit()

# --- Новая функция для проверок интервала ---
def get_upcoming_reminders_window(start: datetime, end: datetime):
    """
    Возвращает заметки, у которых reminder_date между start и end.
    Оба datetime должны быть timezone-aware.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Start и end должны быть timezone-aware datetime")
    
    with SessionLocal() as session:
        notes = session.query(Note).filter(
            Note.reminder_date != None,
            Note.reminder_date >= start,
            Note.reminder_date <= end
        ).order_by(Note.reminder_date).all()
        return notes

# --- Для совместимости старого кода: get_upcoming_reminders ---
def get_upcoming_reminders(hours_before=24, tz_str="Europe/Moscow"):
    """
    Возвращает заметки, у которых reminder_date в ближайшие hours_before часов.
    Для простого использования в командах /upcoming_notes.
    """
    now = datetime.now(tz=ZoneInfo(tz_str))
    end = now + timedelta(hours=hours_before)
    return get_upcoming_reminders_window(now, end)
