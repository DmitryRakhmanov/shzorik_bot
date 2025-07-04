import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Получаем URL базы данных из переменной окружения
# Это критически важно для развертывания на Render.com; не вшивайте URL базы данных в код.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable is not set!")

# Создаем движок базы данных с настройками пула соединений.
# Эти настройки помогают предотвратить неожиданные разрывы соединений.
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    pool_recycle=300,
    pool_pre_ping=True,
    echo=False
)

Base = declarative_base()

class Note(Base):
    """
    Представляет заметку в базе данных.
    Включает поля для ID пользователя, текста заметки, хэштегов и даты напоминания.
    """
    __tablename__ = 'notes'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    hashtags = Column(String) # Хранится как строка хэштегов, разделенных пробелами
    reminder_date = Column(DateTime) # Дата и время события, о котором нужно напомнить

    def __repr__(self):
        return f"<Note(id={self.id}, user_id={self.user_id}, text='{self.text[:20]}...')>"

# Создаем таблицы в базе данных, если они еще не существуют.
Base.metadata.create_all(engine)

# Создаем фабрику сессий, привязанную к нашему настроенному движку.
Session = sessionmaker(bind=engine)

def add_note(user_id: int, text: str, hashtags: str = None, reminder_date: datetime.datetime = None):
    """
    Добавляет новую заметку в базу данных.
    """
    session = Session()
    try:
        new_note = Note(user_id=user_id, text=text, hashtags=hashtags, reminder_date=reminder_date)
        session.add(new_note)
        session.commit()
        return new_note
    except Exception as e:
        session.rollback() # Откатываем изменения в случае ошибки
        raise e
    finally:
        session.close() # Всегда закрываем сессию, чтобы освободить соединение

def update_note_reminder_date(note_id: int):
    """
    Устанавливает reminder_date для конкретной заметки в None,
    тем самым помечая ее как обработанную, чтобы напоминание не отправлялось повторно.
    """
    session = Session()
    try:
        note = session.query(Note).filter(Note.id == note_id).first()
        if note:
            note.reminder_date = None  # Обнуляем дату напоминания
            session.commit()
            return True
        return False
    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()

def find_notes_by_user_and_hashtag(user_id: int, hashtag: str):
    """
    Находит заметки для конкретного пользователя по заданному хэштегу (без учета регистра).
    """
    session = Session()
    notes = session.query(Note).filter(
        Note.user_id == user_id,
        Note.hashtags.ilike(f'%{hashtag}%') # Используем ILIKE для поиска подстроки без учета регистра
    ).all()
    session.close()
    return notes

def initialize_db():
    Base.metadata.create_all(bind=engine)

def get_upcoming_reminders():
    """
    Извлекает напоминания, время уведомления по которым (дата события минус 1 день)
    должно сработать в течение следующих 24 часов от текущего момента.
    """
    session = Session()
    now = datetime.datetime.now()
    
    reminders = session.query(Note).filter(
        Note.reminder_date.isnot(None), # Напоминание должно быть установлено
        # Условие 1: Дата уведомления (дата события минус 1 день) должна быть в будущем
        (Note.reminder_date - datetime.timedelta(days=1)) > now,
        # Условие 2: Дата уведомления должна быть в пределах следующих 24 часов от текущего момента
        (Note.reminder_date - datetime.timedelta(days=1)) <= now + datetime.timedelta(days=1)
    ).all()
    session.close()
    return reminders

def get_all_notes_for_user(user_id: int):
    """
    Извлекает все заметки, хранящиеся для конкретного пользователя.
    """
    session = Session()
    notes = session.query(Note).filter(Note.user_id == user_id).all()
    session.close()
    return notes