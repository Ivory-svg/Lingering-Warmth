"""玩家可见的记忆目录。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from yuwen.store import Store
from yuwen.surfacing import SurfacingService


class CatalogService:
    def __init__(self, store: Store, surfacing: SurfacingService) -> None:
        self.store = store
        self.surfacing = surfacing

    def _item(self, memory: dict[str, Any], edited: bool, now: datetime) -> dict[str, Any]:
        retention = self.surfacing._retention(memory, now)
        return {
            "id": memory["id"],
            "kind": memory["kind"],
            "happened_at": memory["happened_at"],
            "summary": memory["summary"],
            "quote": memory["quote"],
            "emotion_text": memory["emotion_text"],
            "certainty": memory["certainty"],
            "clear": memory["certainty"] >= self.surfacing.config.clear_threshold,
            "retention": round(retention, 2),
            "recall_count": memory["recall_count"],
            "last_recalled_at": memory["last_recalled_at"],
            "sealed": bool(memory["sealed"]),
            "edited": edited,
            "status": memory["status"],
            "status_as_of": memory["status_as_of"],
            "resolved_at": memory["resolved_at"],
            "resolved_note": memory["resolved_note"],
        }

    def list(self, *, filter: str = "all", q: str = "", offset: int = 0, limit: int = 50) -> dict[str, Any]:
        if filter not in {"all", "unclear", "fading", "sealed", "open"}:
            raise ValueError("未知的目录筛选条件")
        if offset < 0 or not 1 <= limit <= 200:
            raise ValueError("目录分页参数无效")
        edited_ids = self.store.memory_ids_with_history()
        rows: list[dict[str, Any]] = []
        now = self.surfacing.clock()
        for memory in self.store.list_catalog_memories():
            sealed = bool(memory["sealed"])
            if filter == "sealed" and not sealed:
                continue
            if filter != "sealed" and sealed:
                continue
            if filter == "unclear" and memory["certainty"] >= self.surfacing.config.clear_threshold:
                continue
            if filter == "fading" and self.surfacing._retention(memory, now) >= 0.2:
                continue
            if filter == "open" and memory["status"] != "open":
                continue
            if q and not any(q in str(memory[field] or "") for field in ("summary", "quote", "emotion_text")):
                continue
            rows.append(memory)
        return {
            "total": len(rows),
            "items": [self._item(row, int(row["id"]) in edited_ids, now) for row in rows[offset:offset + limit]],
        }

    def detail(self, memory_id: int) -> dict[str, Any]:
        memory = self.store.get_memory(memory_id)
        if memory is None or memory["kind"] not in {"turn", "handoff"}:
            raise KeyError(memory_id)
        history = self.store.list_memory_history(memory_id)
        sources = []
        for message in self.store.get_messages_by_ids(json.loads(memory["source_message_ids"])):
            sources.append({
                "message_id": message["id"],
                "role": message["role"],
                "content": self.store.visible_message_content(message),
                "created_at": message["created_at"],
            })
        history_items = []
        for row in history:
            snapshot = json.loads(row["snapshot"])
            if "sealed" in snapshot:
                snapshot["sealed"] = bool(snapshot["sealed"])
            history_items.append({
                "changed_at": row["changed_at"],
                "reason": row["reason"],
                "snapshot": snapshot,
            })
        return {
            **self._item(memory, bool(history), self.surfacing.clock()),
            "sources": sources,
            "history": history_items,
        }

    def correct(
        self, memory_id: int, *, summary: str | None = None,
        quote: str | None = None, emotion_text: str | None = None,
        reason: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        changes: dict[str, str | None] = {}
        for key, value, lower, upper in (
            ("summary", summary, 1, 300),
            ("quote", quote, 0, 200),
            ("emotion_text", emotion_text, 1, 100),
        ):
            if value is None:
                continue
            if not lower <= len(value) <= upper:
                raise ValueError(f"{key} 长度必须是 {lower} 到 {upper} 字")
            changes[key] = None if key == "quote" and value == "" else value
        if not changes:
            raise ValueError("请提供至少一项要改正的内容")
        changed = self.store.correct_memory(
            memory_id, changes, f"玩家改正：{reason}" if reason else "玩家改正"
        )
        return self.detail(memory_id), changed

    def seal(self, memory_id: int, sealed: bool) -> dict[str, Any]:
        self.store.set_memory_sealed(memory_id, sealed)
        return {"id": memory_id, "sealed": sealed}

    def set_status(self, memory_id: int, status: str | None, note: str = "") -> dict[str, Any]:
        self.store.set_memory_status(memory_id, status, note=note)
        return self.detail(memory_id)
