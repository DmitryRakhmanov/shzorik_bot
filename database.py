import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, select, update, BigInteger
from sqlalchemy.orm import sessionmaker, declarative_base

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан")

# ИСПРАВЛЕНИЕ: Используем psycopg (v3) для совместимости с SSL и Python 3.13
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(BigInteger, nullable=False)  # chat_id для канала (отрицательный ID)
    text = Column(String, nullable=False)
    hashtags = Column(String, nullable=True)
    reminder_date = Column(DateTime(timezone=True), nullable=True) 
    reminder_sent = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(ZoneInfo("UTC")))

def init_db():
    Base.metadata.create_all(bind=engine)

def add_note(chat_id: int, text: str, hashtags: str, reminder_date: datetime | None):
    session = SessionLocal()
    try:
        note = Note(chat_id=chat_id, text=text, hashtags=hashtags, reminder_date=reminder_date, reminder_sent=False)
        session.add(note)
        session.commit()
        session.refresh(note)
        return note
    finally:
        session.close()

def get_upcoming_reminders_window(start_time_utc: datetime, end_time_utc: datetime, only_unsent: bool = True):
    session = SessionLocal()
    try:
        stmt = select(Note).where(
            Note.reminder_date.isnot(None),
            Note.reminder_date >= start_time_utc,
            Note.reminder_date <= end_time_utc
        )
        if only_unsent:
            stmt = stmt.where(Note.reminder_sent == False)
        stmt = stmt.order_by(Note.reminder_date)
        return session.execute(stmt).scalars().all()
    finally:
        session.close()

def mark_reminder_sent(note_id: int):
    session = SessionLocal()
    try:
        stmt = update(Note).where(Note.id == note_id).values(reminder_sent=True)
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0
    finally:
        session.close()