import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, select
)
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv

# Загружаем переменные окружения из .env
load_dotenv()

# Получаем DATABASE_URL из env
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задана в переменных окружения!")

# Создаем движок и сессию
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Модель заметки
class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    text = Column(String, nullable=False)
    hashtags = Column(String)
    reminder_date = Column(DateTime, nullable=True)
    reminder_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

# Инициализация базы данных (создает таблицы, если их нет)
def init_db():
    Base.metadata.create_all(bind=engine)

# Функция добавления заметки
def add_note(user_id: int, text: str, hashtags: str = "", reminder_date: datetime = None):
    session = SessionLocal()
    try:
        note = Note(
            user_id=user_id,
            text=text,
            hashtags=hashtags,
            reminder_date=reminder_date,
            reminder_sent=False,
            created_at=datetime.now(ZoneInfo("Europe/Moscow"))
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        return note
    finally:
        session.close()

# Получение напоминаний в заданном окне времени
def get_upcoming_reminders_window(start: datetime, end: datetime, only_unsent=True):
    session = SessionLocal()
    try:
        stmt = select(Note).where(
            Note.reminder_date.isnot(None),
            Note.reminder_date >= start,
            Note.reminder_date <= end
        )
        if only_unsent:
            stmt = stmt.where(Note.reminder_sent == False)
        stmt = stmt.order_by(Note.reminder_date)
        return session.execute(stmt).scalars().all()
    finally:
        session.close()

# Отметить напоминание как отправленное
def mark_reminder_sent(note_id: int):
    session = SessionLocal()
    try:
        note = session.get(Note, note_id)
        if note:
            note.reminder_sent = True
            session.commit()
    finally:
        session.close()
