import os
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Boolean,
    BigInteger,
    select,
    update,
)
from sqlalchemy.orm import sessionmaker, declarative_base

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------- DATABASE CONFIG ----------
DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан")

# psycopg v3 (обязательно для Python 3.12+ и SSL)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://", "postgresql+psycopg://", 1
    )

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,   # важно для долгоживущих сервисов
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)

Base = declarative_base()


# ---------- MODELS ----------
class Note(Base):
    __tablename__ = "notes"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    text = Column(String, nullable=False)
    hashtags = Column(String, nullable=True)

    reminder_date = Column(DateTime(timezone=True), nullable=True)
    reminder_sent = Column(Boolean, default=False)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(ZoneInfo("UTC")),
        nullable=False,
    )


# ---------- INIT ----------
def init_db() -> None:
    """Создать таблицы (без удаления существующих)."""
    Base.metadata.create_all(bind=engine)


# ---------- CRUD ----------
def add_note(
    user_id: int,
    text: str,
    hashtags: str | None,
    reminder_date: datetime | None,
) -> Note:
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


def get_upcoming_reminders_window(
    start_time_utc: datetime,
    end_time_utc: datetime,
    only_unsent: bool = True,
) -> list[Note]:
    session = SessionLocal()
    try:
        stmt = select(Note).where(
            Note.reminder_date.isnot(None),
            Note.reminder_date >= start_time_utc,
            Note.reminder_date <= end_time_utc,
        )

        if only_unsent:
            stmt = stmt.where(Note.reminder_sent.is_(False))

        stmt = stmt.order_by(Note.reminder_date)
        return session.execute(stmt).scalars().all()
    finally:
        session.close()


def mark_reminder_sent(note_id: int) -> bool:
    session = SessionLocal()
    try:
        stmt = (
            update(Note)
            .where(Note.id == note_id)
            .values(reminder_sent=True)
        )
        result = session.execute(stmt)
        session.commit()
        return result.rowcount > 0
    finally:
        session.close()
