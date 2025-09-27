import os
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, select, update
from sqlalchemy.orm import sessionmaker, declarative_base

# Опционально загружаем .env, если есть (для локальной разработки)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv может отсутствовать на GitHub Actions

# Берём URL базы данных из окружения
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан в .env файле или в Secrets GitHub Actions")

# Создаем движок и сессию SQLAlchemy
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Модель Notes
class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False)
    text = Column(String, nullable=False)
    hashtags = Column(String, nullable=True)
    reminder_date = Column(DateTime(timezone=True), nullable=True)
    reminder_sent = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(ZoneInfo("Europe/Moscow")))

# Инициализация базы данных
def init_db():
    Base.metadata.create_all(bind=engine)

# Добавление заметки
def add_note(user_id: int, text: str, hashtags: str, reminder_date: datetime | None):
    session = SessionLocal()
    try:
        note = Note(
            user_id=user_id,
            text=text,
            hashtags=hashtags,
            reminder_date=reminder_date,
            reminder_sent=False,
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        return note
    finally:
        session.close()

# Получение предстоящих напоминаний в окне времени
def get_upcoming_reminders_window(start_time: datetime, end_time: datetime, only_unsent: bool = True):
    session = SessionLocal()
    try:
        stmt = select(Note).where(
            Note.reminder_date.isnot(None),
            Note.reminder_date >= start_time,
            Note.reminder_date <= end_time
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
        stmt = update(Note).where(Note.id == note_id).values(reminder_sent=True)
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0
    finally:
        session.close()
