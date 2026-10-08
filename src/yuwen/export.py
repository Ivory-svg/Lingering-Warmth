"""完整导出玩家的 SQLite 记忆数据。"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from yuwen.store import Store, TABLES, now_iso


def build_export(store: Store) -> dict[str, Any]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in TABLES:
        rows = store.list_table(table)
        for row in rows:
            embedding = row.get("embedding")
            if isinstance(embedding, bytes):
                row["embedding"] = base64.b64encode(embedding).decode("ascii")
        tables[table] = rows
    return {
        "exported_at": now_iso(),
        "schema_version": store.schema_version(),
        "tables": tables,
    }


def _display_time(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")


def build_markdown_export(
    store: Store, *, character_name: str = "他", player_name: str = "你"
) -> str:
    """按对话、记忆、交接和语感笔记导出给人阅读的版本。"""

    lines = [f"# 余温导出 · {now_iso()[:10]}", "", "## 对话", ""]
    for session in store.list_table("sessions"):
        lines.append(f"### 窗口 {session['id']} · {_display_time(session['started_at'])} 起")
        if session["continues_handoff_id"] is not None:
            lines.extend(["", "—— 接续上一段对话 ——"])
        for message in store.list_messages(int(session["id"])):
            label = {
                "user": player_name,
                "assistant": character_name,
                "event": "事件",
            }.get(str(message["role"]), str(message["role"]))
            lines.extend(
                [
                    f"**{label}** {_display_time(message['created_at'])[11:]}",
                    store.visible_message_content(message),
                    "",
                ]
            )
    lines.extend(["## 记忆", ""])
    for memory in store.list_table("memories"):
        lines.append(
            f"- {_display_time(memory['happened_at'])} · {memory['emotion_text']} · "
            f"{memory['summary']}（确定度 {memory['certainty']}）"
        )
    lines.extend(["", "## 交接", ""])
    for handoff in store.list_table("handoffs"):
        lines.extend(
            [
                f"### 窗口 {handoff['session_id']} · {_display_time(handoff['created_at'])}",
                f"正在做：{handoff['doing']}",
                f"聊到：{handoff['where_we_are']}",
                f"此刻情绪：{handoff['emotion']}",
                "",
            ]
        )
    lines.extend(["## 语感笔记", ""])
    for note in store.list_table("voice_notes"):
        state = "已推翻" if note["overturned_at"] else ("启用" if note["active"] else "停用")
        lines.extend(
            [
                f"### 笔记 {note['id']} · {state}",
                f"原句：{note['original']}",
                f"改写：{note['rewritten']}",
                f"原因：{note['reason']}",
            ]
        )
        if note["overturn_note"]:
            lines.append(f"推翻说明：{note['overturn_note']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
