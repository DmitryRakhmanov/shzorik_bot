# database.py
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime
import os

# Получаем URL базы данных из переменной окружения
# Это очень важно для Render.com, так как мы не будем хардкодить URL в коде
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable is not set!")
engine = create_engine(DATABASE_URL, echo=False) # <-- Использует DATABASE_URL
Base = declarative_base()

class Note(Base):
    __tablename__ = 'notes'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    hashtags = Column(String)
    reminder_date = Column(DateTime)

    def __repr__(self):
        return f"<Note(id={self.id}, user_id={self.user_id}, text='{self.text[:20]}...')>"

# Создаем движок базы данных для PostgreSQL
engine = create_engine(DATABASE_URL, echo=False)

Base.metadata.create_all(engine) # Создает таблицы, если их нет

Session = sessionmaker(bind=engine)

def add_note(user_id: int, text: str, hashtags: str = None, reminder_date: datetime.datetime = None):
    session = Session()
    new_note = Note(user_id=user_id, text=text, hashtags=hashtags, reminder_date=reminder_date)
    session.add(new_note)
    session.commit()
    session.close()
    return new_note

def find_notes_by_user_and_hashtag(user_id: int, hashtag: str):
    session = Session()
    notes = session.query(Note).filter(
        Note.user_id == user_id,
        Note.hashtags.ilike(f'%{hashtag}%')
    ).all()
    session.close()
    return notes

def get_upcoming_reminders():
    session = Session()
    now = datetime.datetime.now()
    reminders = session.query(Note).filter(
        Note.reminder_date.isnot(None),
        Note.reminder_date > now,
        Note.reminder_date <= now + datetime.timedelta(days=1)
    ).all()
    session.close()
    return reminders

def get_all_notes_for_user(user_id: int):
    session = Session()
    notes = session.query(Note).filter(Note.user_id == user_id).all()
    session.close()
    return notes