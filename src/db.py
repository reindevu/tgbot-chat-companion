from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class LastUserMessage:
    id: int
    content: str
    created_at: datetime


class Database:
    def __init__(self, sqlite_path: str) -> None:
        self._sqlite_path = sqlite_path
        db_path = Path(sqlite_path)
        if db_path.parent and str(db_path.parent) != ".":
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(sqlite_path)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def init(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_telegram_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                meta_json TEXT,
                is_proactive INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
            """
        )
        self._ensure_message_column("is_proactive", "INTEGER NOT NULL DEFAULT 0")
        self._conn.commit()

    def _ensure_message_column(self, name: str, ddl: str) -> None:
        rows = self._conn.execute("PRAGMA table_info(messages)").fetchall()
        existing = {str(row["name"]) for row in rows}
        if name in existing:
            return
        self._conn.execute(f"ALTER TABLE messages ADD COLUMN {name} {ddl}")

    def get_or_create_active_session(self, owner_telegram_id: int) -> int:
        row = self._conn.execute(
            """
            SELECT id
            FROM sessions
            WHERE owner_telegram_id = ? AND is_active = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (owner_telegram_id,),
        ).fetchone()
        if row:
            return int(row["id"])
        return self.create_new_session(owner_telegram_id)

    def create_new_session(self, owner_telegram_id: int) -> int:
        now = _utc_now_iso()
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET is_active = 0 WHERE owner_telegram_id = ? AND is_active = 1",
                (owner_telegram_id,),
            )
            cursor = self._conn.execute(
                """
                INSERT INTO sessions (owner_telegram_id, created_at, is_active)
                VALUES (?, ?, 1)
                """,
                (owner_telegram_id, now),
            )
        return int(cursor.lastrowid)

    def add_message(
        self,
        session_id: int,
        role: str,
        content: str,
        meta: dict[str, Any] | None = None,
        is_proactive: bool = False,
    ) -> int:
        meta_json = json.dumps(meta, ensure_ascii=True) if meta else None
        cursor = self._conn.execute(
            """
            INSERT INTO messages (session_id, role, content, created_at, meta_json, is_proactive)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, content, _utc_now_iso(), meta_json, int(is_proactive)),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def get_context_messages(self, session_id: int, limit: int) -> list[ChatMessage]:
        rows = self._conn.execute(
            """
            SELECT role, content
            FROM messages
            WHERE session_id = ? AND role IN ('user', 'assistant')
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        rows = list(reversed(rows))
        return [ChatMessage(role=row["role"], content=row["content"]) for row in rows]

    def get_last_user_message(self, session_id: int) -> LastUserMessage | None:
        row = self._conn.execute(
            """
            SELECT id, content, created_at
            FROM messages
            WHERE session_id = ? AND role = 'user'
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return LastUserMessage(
            id=int(row["id"]),
            content=str(row["content"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def has_proactive_after(self, session_id: int, message_id: int) -> bool:
        row = self._conn.execute(
            """
            SELECT id
            FROM messages
            WHERE session_id = ? AND role = 'assistant' AND is_proactive = 1 AND id > ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id, message_id),
        ).fetchone()
        return row is not None

    def export_recent_messages(self, session_id: int, limit: int) -> list[dict[str, str]]:
        rows = self._conn.execute(
            """
            SELECT role, content, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        rows = list(reversed(rows))
        return [
            {
                "role": str(row["role"]),
                "content": str(row["content"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def health(self, owner_telegram_id: int) -> dict[str, Any]:
        active = self._conn.execute(
            """
            SELECT id, created_at
            FROM sessions
            WHERE owner_telegram_id = ? AND is_active = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (owner_telegram_id,),
        ).fetchone()
        total_messages = self._conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
        return {
            "db": "ok",
            "active_session_id": int(active["id"]) if active else None,
            "active_session_created_at": str(active["created_at"]) if active else None,
            "total_messages": int(total_messages),
        }


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
