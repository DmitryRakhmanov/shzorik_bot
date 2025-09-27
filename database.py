import os
import sqlite3
from datetime import datetime
from dataclasses import dataclass

DATABASE_URL = os.environ.get("DATABASE_URL", "bot.db")

@dataclass
class Note:
    id: int
    user_id: int
    text: str
    hashtags: str
    reminder_date: datetime
    sent: bool

def init_db():
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            hashtags TEXT,
            reminder_date TEXT,
            sent INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def add_note(user_id, text, hashtags, reminder_date):
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO notes (user_id, text, hashtags, reminder_date) VALUES (?, ?, ?, ?)",
                   (user_id, text, hashtags, reminder_date.isoformat()))
    conn.commit()
    note_id = cursor.lastrowid
    conn.close()
    return Note(note_id, user_id, text, hashtags, reminder_date, False)

def get_upcoming_reminders_window(start_time, end_time, only_unsent=True):
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    query = "SELECT id, user_id, text, hashtags, reminder_date, sent FROM notes WHERE reminder_date BETWEEN ? AND ?"
    params = (start_time.isoformat(), end_time.isoformat())
    if only_unsent:
        query += " AND sent=0"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()
    return [Note(row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), bool(row[5])) for row in rows]

def mark_reminder_sent(note_id):
    conn = sqlite3.connect(DATABASE_URL)
    cursor = conn.cursor()
    cursor.execute("UPDATE notes SET sent=1 WHERE id=?", (note_id,))
    conn.commit()
    conn.close()
