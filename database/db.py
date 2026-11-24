"""
Database access layer for GTA Reels Automation.
Thread-safe SQLite connection manager with schema initialization.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "storage", "reels.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


class Database:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = os.path.abspath(db_path or DEFAULT_DB_PATH)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self.init_db()

    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self):
        """Initializes tables using schema.sql."""
        if not os.path.exists(SCHEMA_PATH):
            raise FileNotFoundError(f"Schema file not found at {SCHEMA_PATH}")
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        with self.get_connection() as conn:
            conn.executescript(schema_sql)

    # ---------------- Creator Methods ---------------- #
    def add_creator(self, channel_id: str, channel_name: str, permission_proof: str, approved: bool = True) -> int:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO creators (channel_id, channel_name, permission_proof, approved)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(channel_id) DO UPDATE SET
                    channel_name = excluded.channel_name,
                    permission_proof = excluded.permission_proof,
                    approved = excluded.approved
                """,
                (channel_id, channel_name, permission_proof, 1 if approved else 0)
            )
            return cur.lastrowid

    def get_creator(self, channel_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM creators WHERE channel_id = ?", (channel_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_creators(self) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute("SELECT * FROM creators ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def is_creator_approved(self, channel_id: str) -> bool:
        creator = self.get_creator(channel_id)
        return bool(creator and creator.get("approved") == 1)

    # ---------------- VOD Methods ---------------- #
    def add_or_get_vod(self, youtube_id: str, channel_id: str, title: str, duration: int) -> int:
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM vods WHERE youtube_id = ?", (youtube_id,)
            ).fetchone()
            if row:
                return row["id"]
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO vods (youtube_id, channel_id, title, duration, processed)
                VALUES (?, ?, ?, ?, 0)
                """,
                (youtube_id, channel_id, title, duration)
            )
            return cur.lastrowid

    def mark_vod_processed(self, vod_id: int, status: int = 1):
        with self.get_connection() as conn:
            conn.execute("UPDATE vods SET processed = ? WHERE id = ?", (status, vod_id))

    # ---------------- Clip Methods ---------------- #
    def add_clip(
        self,
        vod_id: int,
        start_time: float,
        end_time: float,
        score: float,
        raw_file_path: Optional[str] = None
    ) -> int:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO clips (vod_id, start_time, end_time, score, raw_file_path, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (vod_id, start_time, end_time, score, raw_file_path)
            )
            return cur.lastrowid

    def update_clip_metadata(
        self,
        clip_id: int,
        rendered_file_path: Optional[str] = None,
        hook_text: Optional[str] = None,
        commentary_text: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[str] = None
    ):
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE clips SET
                    rendered_file_path = COALESCE(?, rendered_file_path),
                    hook_text = COALESCE(?, hook_text),
                    commentary_text = COALESCE(?, commentary_text),
                    title = COALESCE(?, title),
                    description = COALESCE(?, description),
                    tags = COALESCE(?, tags)
                WHERE id = ?
                """,
                (rendered_file_path, hook_text, commentary_text, title, description, tags, clip_id)
            )

    def update_clip_status(self, clip_id: int, status: str, telegram_message_id: Optional[int] = None):
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE clips SET
                    status = ?,
                    telegram_message_id = COALESCE(?, telegram_message_id)
                WHERE id = ?
                """,
                (status, telegram_message_id, clip_id)
            )

    def get_clip(self, clip_id: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
            return dict(row) if row else None

    def get_clips_by_status(self, status: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM clips WHERE status = ? ORDER BY id ASC", (status,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------------- Upload Log Methods ---------------- #
    def log_upload(self, clip_id: int, youtube_video_id: str, youtube_url: str, privacy_status: str) -> int:
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO upload_logs (clip_id, youtube_video_id, youtube_url, privacy_status)
                VALUES (?, ?, ?, ?)
                """,
                (clip_id, youtube_video_id, youtube_url, privacy_status)
            )
            conn.execute("UPDATE clips SET status = 'uploaded' WHERE id = ?", (clip_id,))
            return cur.lastrowid

    def get_daily_upload_count(self) -> int:
        """Returns the number of videos uploaded in the current calendar day (UTC)."""
        today_start = datetime.utcnow().strftime("%Y-%m-%d 00:00:00")
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM upload_logs WHERE uploaded_at >= ?", (today_start,)
            ).fetchone()
            return row["cnt"] if row else 0
