"""完整导出玩家的 SQLite 记忆数据。"""

from __future__ import annotations

import base64
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
