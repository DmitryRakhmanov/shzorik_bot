import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, select, update
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL")
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Moscow")

Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True)
    text = Column(String, nullable=False)
    hashtags = Column(String, nullable=True)
    reminder_date = Column(DateTime(timezone=True), nullable=True)
    reminder_sent = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(ZoneInfo(TIMEZONE)))


def initialize_db():
    Base.metadata.create_all(bind=engine)


def add_note(user_id, text, hashtags=None, reminder_date=None):
    with SessionLocal() as session:
        if reminder_date and reminder_date.tzinfo is None:
            reminder_date = reminder_date.replace(tzinfo=ZoneInfo(TIMEZONE))
        note = Note(user_id=user_id, text=text, hashtags=hashtags, reminder_date=reminder_date)
        session.add(note)
        session.commit()
        session.refresh(note)
        return note


def find_notes_by_user_and_hashtag(user_id, hashtag):
    with SessionLocal() as session:
        stmt = select(Note).where(Note.user_id == user_id, Note.hashtags.like(f"%{hashtag}%"))
        return session.execute(stmt).scalars().all()


def get_all_notes_for_user(user_id):
    with SessionLocal() as session:
        stmt = select(Note).where(Note.user_id == user_id).order_by(Note.created_at.desc())
        return session.execute(stmt).scalars().all()


def update_note_reminder_date(note_id, new_reminder_date=None, sent=False):
    with SessionLocal() as session:
        stmt = update(Note).where(Note.id == note_id)
        values = {}
        if new_reminder_date:
            values["reminder_date"] = new_reminder_date
        if sent:
            values["reminder_sent"] = True
        stmt = stmt.values(**values)
        session.execute(stmt)
        session.commit()


def get_upcoming_reminders_window(start_dt, end_dt, only_unsent=True):
    tz = ZoneInfo(TIMEZONE)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=tz)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=tz)

    with SessionLocal() as session:
        stmt = select(Note).where(
            Note.reminder_date != None,
            Note.reminder_date >= start_dt,
            Note.reminder_date <= end_dt
        )
        if only_unsent:
            stmt = stmt.where(Note.reminder_sent == False)
        stmt = stmt.order_by(Note.reminder_date)
        return session.execute(stmt).scalars().all()


def get_past_unsent_reminders():
    """Вернуть напоминания, которые должны были быть отправлены, но reminder_sent=False"""
    now = datetime.now(ZoneInfo(TIMEZONE))
    with SessionLocal() as session:
        stmt = select(Note).where(
            Note.reminder_date != None,
            Note.reminder_date <= now,
            Note.reminder_sent == False
        ).order_by(Note.reminder_date)
        return session.execute(stmt).scalars().all()
