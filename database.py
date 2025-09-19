from sqlalchemy import create_engine, Column, Integer, BigInteger, Text, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Настройка базы данных
DATABASE_URL = "postgresql://username:password@hostname:port/dbname"  # Замени на свои данные

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()

# Модель Notes
class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, nullable=False)
    text = Column(Text, nullable=False)
    hashtags = Column(Text)
    reminder_date = Column(DateTime(timezone=True))
    reminder_sent = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(tz=ZoneInfo("Europe/Moscow")))

# Инициализация базы
def init_db():
    Base.metadata.create_all(bind=engine)
    print("Database initialized.")

# Добавление заметки
def add_note(user_id: int, text: str, hashtags: str = None, reminder_date: datetime = None) -> Note:
    session = SessionLocal()
    try:
        note = Note(
            user_id=user_id,
            text=text,
            hashtags=hashtags,
            reminder_date=reminder_date,
            reminder_sent=False
        )
        session.add(note)
        session.commit()
        session.refresh(note)
        print(f"Saved reminder for user {user_id} at {reminder_date}")
        return note
    except SQLAlchemyError as e:
        session.rollback()
        print("Error adding note:", e)
        raise
    finally:
        session.close()

# Получение предстоящих напоминаний
def get_upcoming_reminders_window(start: datetime, end: datetime, only_unsent=True):
    session = SessionLocal()
    try:
        tz = ZoneInfo("Europe/Moscow")
        start = start.astimezone(tz)
        end = end.astimezone(tz)

        query = session.query(Note).filter(Note.reminder_date != None).filter(
            Note.reminder_date >= start,
            Note.reminder_date <= end
        )
        if only_unsent:
            query = query.filter(Note.reminder_sent == False)

        return query.order_by(Note.reminder_date).all()
    finally:
        session.close()

# Отметить напоминание как отправленное
def mark_reminder_sent(note_id: int):
    session = SessionLocal()
    try:
        note = session.query(Note).get(note_id)
        if note:
            note.reminder_sent = True
            session.commit()
            print(f"Marked reminder {note_id} as sent.")
    finally:
        session.close()
