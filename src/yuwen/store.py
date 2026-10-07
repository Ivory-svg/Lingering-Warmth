"""SQLite 持久化；项目内只有这个模块执行 SQL。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from importlib.resources import files
import json
from pathlib import Path
import sqlite3
from typing import Any


TABLES = (
    "schema_meta",
    "sessions",
    "messages",
    "memories",
    "memory_history",
    "handoffs",
    "voice_notes",
)


def now_iso() -> str:
    """返回带时区的 ISO 8601 时间。"""

    return datetime.now(timezone.utc).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    """用短连接封装全部 SQLite 读写，便于后台任务安全使用。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        """建立数据库，并执行当前唯一的一号迁移。"""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        migration = (
            files("yuwen").joinpath("migrations", "001_initial.sql").read_text(encoding="utf-8")
        )
        with self._connect() as connection:
            connection.executescript(migration)

    def schema_version(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'version'"
            ).fetchone()
        if row is None:
            raise RuntimeError("数据库尚未初始化")
        return str(row["value"])

    def create_session(
        self,
        continues_handoff_id: int | None = None,
        *,
        started_at: str | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO sessions (started_at, continues_handoff_id) VALUES (?, ?)",
                (started_at or now_iso(), continues_handoff_id),
            )
            return int(cursor.lastrowid)

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_active_session(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_or_create_active_session(self) -> dict[str, Any]:
        session = self.get_active_session()
        if session is not None:
            return session
        session_id = self.create_session()
        created = self.get_session(session_id)
        assert created is not None
        return created

    def close_session(self, session_id: int, *, ended_at: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (ended_at or now_iso(), session_id),
            )

    def add_message(
        self,
        session_id: int,
        role: str,
        content: str,
        *,
        meta: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (session_id, role, content, created_at, meta)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    role,
                    content,
                    created_at or now_iso(),
                    _json(meta) if meta is not None else None,
                ),
            )
            return int(cursor.lastrowid)

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
        return dict(row) if row else None

    def update_message_meta(self, message_id: int, meta: dict[str, Any]) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE messages SET meta = ? WHERE id = ?",
                (_json(meta), message_id),
            )
            return cursor.rowcount > 0

    def list_messages(self, session_id: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_tail_messages(self, session_id: int, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
                ) ORDER BY id
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_messages_by_ids(self, message_ids: Sequence[int]) -> list[dict[str, Any]]:
        if not message_ids:
            return []
        placeholders = ",".join("?" for _ in message_ids)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM messages WHERE id IN ({placeholders}) ORDER BY id",  # noqa: S608
                tuple(message_ids),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_memory(
        self,
        *,
        kind: str,
        happened_at: str,
        summary: str,
        emotion_text: str,
        certainty: float,
        source_message_ids: Sequence[int],
        session_id: int,
        quote: str | None = None,
        valence: float | None = None,
        arousal: float | None = None,
        created_at: str | None = None,
        embedding: bytes | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memories (
                    kind, created_at, happened_at, summary, quote, emotion_text,
                    valence, arousal, certainty, source_message_ids, session_id,
                    embedding
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kind,
                    created_at or now_iso(),
                    happened_at,
                    summary,
                    quote,
                    emotion_text,
                    valence,
                    arousal,
                    certainty,
                    _json(list(source_message_ids)),
                    session_id,
                    embedding,
                ),
            )
            return int(cursor.lastrowid)

    def get_memory(self, memory_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
        return dict(row) if row else None

    def add_memory_history(
        self,
        memory_id: int,
        snapshot: dict[str, Any],
        *,
        reason: str | None = None,
        changed_at: str | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memory_history (memory_id, snapshot, changed_at, reason)
                VALUES (?, ?, ?, ?)
                """,
                (memory_id, _json(snapshot), changed_at or now_iso(), reason),
            )
            return int(cursor.lastrowid)

    def add_handoff(
        self,
        *,
        session_id: int,
        doing: str,
        where_we_are: str,
        emotion: str,
        raw_tail_message_ids: Sequence[int],
        created_at: str | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO handoffs (
                    session_id, created_at, doing, where_we_are, emotion,
                    raw_tail_message_ids
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    created_at or now_iso(),
                    doing,
                    where_we_are,
                    emotion,
                    _json(list(raw_tail_message_ids)),
                ),
            )
            return int(cursor.lastrowid)

    def get_handoff(self, handoff_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
        return dict(row) if row else None

    def add_voice_note(
        self,
        *,
        source: str,
        original: str,
        rewritten: str,
        reason: str,
        message_id: int | None = None,
        created_at: str | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO voice_notes (
                    created_at, source, message_id, original, rewritten, reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at or now_iso(),
                    source,
                    message_id,
                    original,
                    rewritten,
                    reason,
                ),
            )
            return int(cursor.lastrowid)

    def list_voice_notes(self, *, active_only: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
        conditions = "WHERE active = 1" if active_only else ""
        limit_sql = "LIMIT ?" if limit is not None else ""
        parameters: tuple[object, ...] = (limit,) if limit is not None else ()
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM voice_notes {conditions} ORDER BY id DESC {limit_sql}",  # noqa: S608
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def update_voice_note_active(self, note_id: int, active: bool) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE voice_notes SET active = ? WHERE id = ?",
                (int(active), note_id),
            )
            return cursor.rowcount > 0

    def list_table(self, table: str) -> list[dict[str, Any]]:
        """为完整导出读取白名单内的一张表。"""

        if table not in TABLES:
            raise ValueError(f"不支持导出表：{table}")
        order_column = "key" if table == "schema_meta" else "id"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table} ORDER BY {order_column}"
            ).fetchall()
        return [dict(row) for row in rows]
