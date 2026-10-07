import base64
from pathlib import Path

from fastapi.testclient import TestClient

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

    assert exported["schema_version"] == "1"
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

    assert health.json() == {"ok": True, "schema_version": "1"}
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    assert set(exported.json()["tables"]) == set(TABLES)
