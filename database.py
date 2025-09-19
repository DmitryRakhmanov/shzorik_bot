import os
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable is not set!")

Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine)

class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)  # None для каналов
    text = Column(String, nullable=False)
    hashtags = Column(String, nullable=True)
    reminder_date = Column(DateTime(timezone=True), nullable=True)

def initialize_db():
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/checked.")

def add_note(user_id, text, hashtags, reminder_date=None):
    session = SessionLocal()
    try:
        note = Note(
            user_id=user_id,
            text=text,
            hashtags=hashtags,
            reminder_date=reminder_date
        )
        session.add(note)
        session.commit()
        logger.info(f"Note saved: {text}, reminder={reminder_date}")
    except Exception as e:
        logger.error(f"Error adding note: {e}")
        session.rollback()
    finally:
        session.close()

def find_notes_by_user_and_hashtag(user_id, hashtag):
    session = SessionLocal()
    try:
        notes = session.query(Note).filter(
            Note.user_id == user_id,
            Note.hashtags.like(f"%{hashtag}%")
        ).all()
        return notes
    finally:
        session.close()

def get_all_notes_for_user(user_id):
    session = SessionLocal()
    try:
        notes = session.query(Note).filter(Note.user_id == user_id).all()
        return notes
    finally:
        session.close()

def update_note_reminder_date(note_id, new_date=None):
    session = SessionLocal()
    try:
        note = session.query(Note).filter(Note.id == note_id).first()
        if note:
            note.reminder_date = new_date
            session.commit()
            logger.info(f"Updated reminder for note {note_id} to {new_date}")
    except Exception as e:
        logger.error(f"Error updating note {note_id}: {e}")
        session.rollback()
    finally:
        session.close()

def get_upcoming_reminders_window(start_dt, end_dt):
    """Возвращает напоминания в указанном временном окне (datetime aware)"""
    session = SessionLocal()
    try:
        tz_str = os.environ.get("TIMEZONE", "Europe/Moscow")
        start_dt = start_dt.replace(tzinfo=ZoneInfo(tz_str))
        end_dt = end_dt.replace(tzinfo=ZoneInfo(tz_str))

        notes = session.query(Note).filter(
            Note.reminder_date >= start_dt,
            Note.reminder_date <= end_dt
        ).all()
        return notes
    finally:
        session.close()
