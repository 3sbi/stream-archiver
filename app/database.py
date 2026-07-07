import sqlite3
from pathlib import Path
from app.config import Config


class Database:
    def __init__(self):
        Path(Config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(Config.DB_PATH, check_same_thread=False)
        self.create_tables()

    def get_uploaded_files(self):
        cur = self.conn.cursor()
        cur.execute("""
        SELECT filename FROM uploaded_segments
        """)
        return {row[0] for row in cur.fetchall()}

    def create_tables(self):
        cur = self.conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS streams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT UNIQUE,
            title TEXT,
            started_at TEXT,
            ended_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS uploaded_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE,
            telegram_message_id INTEGER,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        self.conn.commit()

    def create_stream(self, session_id: str, title: str, started_at: str):
        cur = self.conn.cursor()
        cur.execute(
            """
        INSERT OR IGNORE INTO streams
        (
            session_id,
            title,
            started_at
        )
        VALUES (?, ?, ?)
        """,
            (session_id, title, started_at),
        )
        self.conn.commit()

    def update_title(self, session_id: str, title: str):
        cur = self.conn.cursor()
        cur.execute(
            """
        UPDATE streams
        SET title=?
        WHERE session_id=?
        """,
            (title, session_id),
        )
        self.conn.commit()

    def finish_stream(self, session_id: str, ended_at: str):
        cur = self.conn.cursor()
        cur.execute(
            """
        UPDATE streams
        SET ended_at=?
        WHERE session_id=?
        """,
            (ended_at, session_id),
        )
        self.conn.commit()

    def is_uploaded(self, filename: str):
        cur = self.conn.cursor()
        cur.execute(
            """
        SELECT 1
        FROM uploaded_segments
        WHERE filename=?
        """,
            (filename,),
        )
        return cur.fetchone() is not None

    def mark_uploaded(self, filename: str, message_id: int | None):
        cur = self.conn.cursor()
        cur.execute(
            """
        INSERT OR IGNORE INTO uploaded_segments
        (
            filename,
            telegram_message_id
        )
        VALUES (?, ?)
        """,
            (filename, message_id),
        )
        self.conn.commit()


db = Database()
