import base64
from dataclasses import replace
from pathlib import Path

from authed_client import TestClient

from test_chat_api import FakeLLM, make_parts
from yuwen.api import create_app
from yuwen.export import build_export
from yuwen.store import TABLES, Store


def seed_all_business_tables(store: Store) -> None:
    session_id = store.create_session()
    message_id = store.add_message(session_id, "assistant", "回复")
    memory_id = store.add_memory(
        kind="turn",
        happened_at="2026-10-07T10:00:00+08:00",
        summary="一轮对话",
        emotion_text="平静",
        certainty=0.9,
        source_message_ids=[message_id],
        session_id=session_id,
        embedding=b"\x00\x01\x02",
    )
    store.add_memory_history(memory_id, {"id": memory_id, "summary": "一轮对话"})
    handoff_id = store.add_handoff(
        session_id=session_id,
        doing="聊天",
        where_we_are="刚开始",
        emotion="平静",
        raw_tail_message_ids=[message_id],
    )
    store.add_voice_note(
        source="player",
        message_id=message_id,
        original="原句",
        rewritten="改句",
        reason="语气",
    )
    store.close_session(session_id)
    store.create_session(continues_handoff_id=handoff_id)


def test_build_export_contains_every_table_and_base64_embedding(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    seed_all_business_tables(store)

    exported = build_export(store)

    assert exported["schema_version"] == "4"
    assert set(exported["tables"]) == set(TABLES)
    assert exported["tables"]["memories"][0]["embedding"] == base64.b64encode(
        b"\x00\x01\x02"
    ).decode("ascii")
    assert all(exported["tables"][table] for table in TABLES)


def test_export_and_health_endpoints(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    seed_all_business_tables(store)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        health = client.get("/health")
        exported = client.get("/export")
        markdown = client.get("/export?format=markdown")

    assert health.json() == {"ok": True, "schema_version": "4"}
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    assert set(exported.json()["tables"]) == set(TABLES)
    assert "auth_sessions" not in exported.json()["tables"]
    assert "—— 续写 · 新的一卷 ——" in markdown.text


def test_markdown_export_uses_names_and_restored_reply_without_embedding(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    config = replace(
        config, character=replace(config.character, name="林舟", player_name="小雨")
    )
    session_id = store.create_session(started_at="2026-10-07T10:00:00+08:00")
    store.add_message(session_id, "user", "你想我吗？")
    assistant_id = store.add_message(session_id, "assistant", "……今天怎么没来。")
    note_id = store.add_voice_note(
        source="gatekeeper",
        message_id=assistant_id,
        original="我好想你！",
        rewritten="……今天怎么没来。",
        reason="太直白",
    )
    store.overturn_voice_note(note_id, "他会这样说")
    memory_id = store.add_memory(
        kind="turn",
        happened_at="2026-10-07T10:01:00+08:00",
        summary="她问他想不想她",
        emotion_text="想念",
        certainty=0.9,
        source_message_ids=[assistant_id],
        session_id=session_id,
        embedding=b"secret-embedding",
    )
    store.mark_memories_recalled([memory_id], recalled_at="2026-10-08T10:00:00+08:00")
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        response = client.get("/export?format=markdown")
        invalid = client.get("/export?format=unknown")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert "yuwen-export.md" in response.headers["content-disposition"]
    assert "**你**" in response.text.split("## 随笔")[0]
    assert "**小雨**" not in response.text.split("## 随笔")[0]
    assert "**林舟**" in response.text
    assert "我好想你！" in response.text.split("## 随笔")[0]
    assert "# 余温 · 全书导出" in response.text
    assert "### 第 1 卷" in response.text
    assert "## 随笔" in response.text
    assert "## 续写" in response.text
    assert "## 勘误" in response.text
    assert "被想起 1 次" in response.text
    assert "secret-embedding" not in response.text
    assert invalid.status_code == 422
