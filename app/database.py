import sqlite3
from contextlib import closing
from pathlib import Path

from app.config import Config


class Database:
    def __init__(self):
        self.path = Config.DB_PATH
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
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
        cur.execute("""
        CREATE TABLE IF NOT EXISTS group_upload_attempts (
            filename TEXT PRIMARY KEY,
            attempted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            rejected INTEGER NOT NULL DEFAULT 0
        )
        """)
        cur.execute("PRAGMA table_info(group_upload_attempts)")
        columns = {row[1] for row in cur.fetchall()}
        if "rejected" not in columns:
            cur.execute(
                "ALTER TABLE group_upload_attempts "
                "ADD COLUMN rejected INTEGER NOT NULL DEFAULT 0"
            )
        self.conn.commit()

    def claim_group_upload(self, filenames: list[str]) -> bool:
        # A separate transaction makes the claim atomic across workers and restarts.
        # A rejected claim (definitely not delivered) is retried by startup recovery.
        with closing(sqlite3.connect(self.path)) as conn:
            try:
                with conn:
                    conn.executemany(
                        "INSERT INTO group_upload_attempts (filename) VALUES (?)",
                        [(filename,) for filename in filenames],
                    )
            except sqlite3.IntegrityError:
                return False
        return True

    def reject_group_upload(self, filenames: list[str]) -> None:
        """Mark a confirmed, non-delivered failure for retry on the next start."""
        if not filenames:
            return
        with closing(sqlite3.connect(self.path)) as conn:
            with conn:
                conn.executemany(
                    "UPDATE group_upload_attempts SET rejected = 1 WHERE filename = ?",
                    [(filename,) for filename in filenames],
                )

    def release_group_upload(self, filenames: list[str]) -> None:
        """Remove claims so the files can be claimed and attempted again."""
        if not filenames:
            return
        with closing(sqlite3.connect(self.path)) as conn:
            with conn:
                conn.executemany(
                    "DELETE FROM group_upload_attempts WHERE filename = ?",
                    [(filename,) for filename in filenames],
                )

    def get_group_upload_attempts(self) -> set[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT filename FROM group_upload_attempts")
        return {row[0] for row in cur.fetchall()}

    def get_rejected_group_uploads(self) -> set[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT filename FROM group_upload_attempts WHERE rejected = 1")
        return {row[0] for row in cur.fetchall()}

    def get_stream(self, session_id: str) -> tuple[str, str] | None:
        cur = self.conn.cursor()
        cur.execute(
            """
        SELECT title, started_at
        FROM streams
        WHERE session_id=?
        """,
            (session_id,),
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else None

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
