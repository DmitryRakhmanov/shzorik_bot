import os
import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Get database URL from environment variable
# This is crucial for Render.com deployments; do not hardcode your database URL.
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise Exception("DATABASE_URL environment variable is not set!")

# Create the database engine with connection pooling settings.
# These settings help prevent unexpected disconnections (e.g., "SSL connection has been closed unexpectedly").
# pool_size: Maximum number of connections to keep in the pool.
# max_overflow: Additional connections that can be opened if the pool is busy.
# pool_recycle: Time in seconds after which connections are recycled (re-established).
#               Set this lower than your database's idle timeout (e.g., 300 seconds for 5 minutes).
# pool_pre_ping: Sends a lightweight query to the DB before using a connection to ensure it's still active.
# echo=False: Disables verbose SQL query logging to the console (set to True for debugging if needed).
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
    Represents a note in the database.
    Includes fields for user ID, note text, hashtags, and a reminder date.
    """
    __tablename__ = 'notes'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    hashtags = Column(String) # Stored as a space-separated string of hashtags
    reminder_date = Column(DateTime)

    def __repr__(self):
        return f"<Note(id={self.id}, user_id={self.user_id}, text='{self.text[:20]}...')>"

# Create tables in the database if they don't already exist.
# This should ideally be called once during application initialization or deployment.
Base.metadata.create_all(engine)

# Create a session factory bound to our configured engine.
Session = sessionmaker(bind=engine)

def add_note(user_id: int, text: str, hashtags: str = None, reminder_date: datetime.datetime = None):
    """
    Adds a new note to the database.
    Includes basic error handling and session management.
    """
    session = Session()
    try:
        new_note = Note(user_id=user_id, text=text, hashtags=hashtags, reminder_date=reminder_date)
        session.add(new_note)
        session.commit()
        # session.refresh(new_note) # Uncomment if you need generated ID immediately after commit
        return new_note
    except Exception as e:
        session.rollback() # Rollback changes on error
        raise e
    finally:
        session.close() # Always close the session to release the connection

def find_notes_by_user_and_hashtag(user_id: int, hashtag: str):
    """
    Finds notes for a specific user by a given hashtag (case-insensitive).
    """
    session = Session()
    notes = session.query(Note).filter(
        Note.user_id == user_id,
        Note.hashtags.ilike(f'%{hashtag}%') # Uses ILIKE for case-insensitive substring search
    ).all()
    session.close()
    return notes

def get_upcoming_reminders():
    """
    Retrieves reminders that are set to trigger within the next 24 hours from the current time.
    """
    session = Session()
    now = datetime.datetime.now()
    reminders = session.query(Note).filter(
        Note.reminder_date.isnot(None),
        Note.reminder_date > now, # Reminder must be in the future
        Note.reminder_date <= now + datetime.timedelta(days=1) # Reminder must be within the next 24 hours
    ).all()
    session.close()
    return reminders

def get_all_notes_for_user(user_id: int):
    """
    Retrieves all notes stored for a specific user.
    """
    session = Session()
    notes = session.query(Note).filter(Note.user_id == user_id).all()
    session.close()
    return notes