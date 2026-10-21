"""目录筛选、原话、改正和封页。"""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from authed_client import TestClient
from fastapi.testclient import TestClient as AnonymousClient
from test_chat_api import FakeLLM, make_parts
from test_embedder import FakeEmbedding
from yuwen.api import create_app
from yuwen.config import EmbeddingConfig


def _memory(store, session_id, summary, *, certainty=0.9, sealed=False,
            happened_at="2026-10-12T10:00:00+00:00", quote=None, emotion="平静", kind="turn"):
    return store.add_memory(
        kind=kind, happened_at=happened_at, summary=summary, quote=quote,
        emotion_text=emotion, certainty=certainty, source_message_ids=[],
        session_id=session_id, sealed=sealed,
    )


def test_catalog_filters_search_pagination_retention_and_excludes_dream(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    session_id = store.create_session()
    new = _memory(store, session_id, "新近的雨伞", quote="今天下雨", emotion="高兴")
    old = _memory(store, session_id, "旧日的车票", certainty=0.4,
                  happened_at="2026-07-01T10:00:00+00:00")
    sealed = _memory(store, session_id, "封起来的信", sealed=True)
    _memory(store, session_id, "梦到海边", kind="dream")
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)
    now = datetime(2026, 10, 13, tzinfo=timezone.utc)
    app.state.surfacing_service.clock = lambda: now

    with TestClient(app) as client:
        all_items = client.get("/memories").json()
        assert all_items["total"] == 2
        assert [item["id"] for item in all_items["items"]] == [new, old]
        assert all_items["items"][0]["retention"] == round(
            app.state.surfacing_service._retention(store.get_memory(new), now), 2
        )
        assert all_items["items"][0]["clear"] is True
        assert client.get("/memories?filter=unclear").json()["items"][0]["id"] == old
        assert client.get("/memories?filter=fading").json()["items"][0]["id"] == old
        assert client.get("/memories?filter=sealed").json()["items"][0]["id"] == sealed
        for word in ("雨伞", "下雨", "高兴"):
            assert client.get("/memories", params={"q": word}).json()["items"][0]["id"] == new
        assert client.get("/memories", params={"offset": 1, "limit": 1}).json()["items"][0]["id"] == old
        assert client.get("/memories?limit=201").status_code == 422
        assert client.get("/memories?filter=other").status_code == 422
        assert client.get("/memories/999").status_code == 404


def test_correction_history_sources_and_sealing_never_delete_memory(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    old_session = store.create_session()
    user_id = store.add_message(old_session, "user", "雨伞在哪里？")
    assistant_id = store.add_message(old_session, "assistant", "在门口。")
    note_id = store.add_voice_note(
        source="gatekeeper", message_id=assistant_id,
        original="在书桌上。", rewritten="在门口。", reason="校对看走眼",
    )
    store.overturn_voice_note(note_id, None)
    memory_id = store.add_memory(
        kind="turn", happened_at="2026-10-01T10:00:00+00:00", summary="雨伞放在门口",
        quote="在门口。", emotion_text="安心", certainty=0.5,
        source_message_ids=[user_id, assistant_id], session_id=old_session,
        embedding=b"old-vector",
    )
    store.close_session(old_session)
    active = store.create_session()
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with TestClient(app) as client:
        detail = client.get(f"/memories/{memory_id}").json()
        assert [source["content"] for source in detail["sources"]] == ["雨伞在哪里？", "在书桌上。"]
        edited = client.patch(f"/memories/{memory_id}", json={
            "summary": "雨伞其实放在书桌上", "quote": "", "reason": "不是门口"
        })
        assert edited.status_code == 200
        assert edited.json()["certainty"] == 1.0
        assert edited.json()["quote"] is None
        assert edited.json()["edited"] is True
        assert edited.json()["history"][0]["reason"] == "玩家改正：不是门口"
        assert "embedding" not in edited.json()["history"][0]["snapshot"]
        assert store.get_memory(memory_id)["embedding"] is None
        assert store.get_memory(memory_id)["embedding_model"] is None
        assert client.patch(f"/memories/{memory_id}", json={"summary": "雨伞其实放在书桌上"}).status_code == 200
        assert len(store.list_memory_history(memory_id)) == 1
        assert client.patch(f"/memories/{memory_id}", json={"summary": "x" * 301}).status_code == 422
        assert client.patch(f"/memories/{memory_id}", json={"quote": "x" * 201}).status_code == 422
        assert client.patch(f"/memories/{memory_id}", json={"emotion_text": ""}).status_code == 422
        assert client.patch(f"/memories/{memory_id}", json={"summary": None}).status_code == 422
        assert client.get("/memories?filter=unclear").json()["total"] == 0

        assert asyncio.run(app.state.surfacing_service.find(active, "雨伞")) is not None
        assert client.post(f"/memories/{memory_id}/seal", json={"sealed": True}).json() == {
            "id": memory_id, "sealed": True
        }
        assert asyncio.run(app.state.surfacing_service.find(active, "雨伞")) is None
        assert client.get("/memories?filter=sealed").json()["total"] == 1
        assert client.post(f"/memories/{memory_id}/seal", json={"sealed": True}).status_code == 200
        assert len(store.list_memory_history(memory_id)) == 2
        assert client.post(f"/memories/{memory_id}/seal", json={"sealed": False}).json()["sealed"] is False
        assert asyncio.run(app.state.surfacing_service.find(active, "雨伞")) is not None
        assert [row["reason"] for row in store.list_memory_history(memory_id)] == ["撕开", "封页", "玩家改正：不是门口"]
        assert store.get_memory(memory_id) is not None
        assert len(store.list_table("memories")) == 1


def test_catalog_page_and_correction_reembed_with_fake_model(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    config = replace(config, embedding=EmbeddingConfig("https://fake.test", model="fake-model"))
    session_id = store.create_session()
    memory_id = _memory(store, session_id, "原来的摘要")
    fake = FakeEmbedding()
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books, embedding_client=fake)

    with TestClient(app) as client:
        page = client.get("/catalog")
        assert page.status_code == 200
        for label in ("全部", "记不太清的", "快褪掉的", "封页的", "搜索目录", "撕开", "用名字称呼两个人"):
            assert label in page.text
        result = client.patch(f"/memories/{memory_id}", json={"summary": "改正后的摘要"})
        assert result.status_code == 200

    assert ["改正后的摘要"] in fake.calls
    assert store.get_memory(memory_id)["embedding_model"] == "fake-model"


def test_todo_status_endpoint_filter_history_and_validation(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    session_id = store.create_session()
    memory_id = _memory(store, session_id, "周五要看牙")
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)

    with AnonymousClient(app) as anonymous:
        assert anonymous.post(f"/memories/{memory_id}/status", json={"status": "open"}).status_code == 401
    with TestClient(app) as client:
        endpoint = f"/memories/{memory_id}/status"
        assert client.get("/memories?filter=open").json()["total"] == 0
        opened = client.post(endpoint, json={"status": "open"}).json()
        assert opened["status"] == "open" and opened["status_as_of"] is not None
        assert opened["resolved_at"] is None and opened["resolved_note"] is None
        assert opened["history"][0]["reason"] == "玩家标成待续"
        assert client.get("/memories?filter=open").json()["items"][0]["id"] == memory_id
        assert client.post(endpoint, json={"status": "open", "note": "无须修改"}).status_code == 200
        assert len(store.list_memory_history(memory_id)) == 1

        done = client.post(endpoint, json={"status": "done", "note": "看完了"}).json()
        assert done["status"] == "done" and done["resolved_note"] == "看完了"
        assert done["status_as_of"] == done["resolved_at"]
        assert done["history"][0]["reason"] == "玩家标完结：看完了"
        assert done["history"][0]["snapshot"]["status"] == "open"
        assert "embedding" not in done["history"][0]["snapshot"]
        assert client.get("/memories?filter=open").json()["total"] == 0

        reopened = client.post(endpoint, json={"status": "open"}).json()
        assert reopened["history"][0]["reason"] == "玩家重新打开"
        assert reopened["resolved_at"] is None and reopened["resolved_note"] is None

        dropped = client.post(endpoint, json={"status": "dropped", "note": "电影下架了"}).json()
        assert dropped["history"][0]["reason"] == "玩家标不做了：电影下架了"
        assert dropped["resolved_note"] == "电影下架了"
        cleared = client.post(endpoint, json={"status": None}).json()
        assert cleared["history"][0]["reason"] == "玩家说不是待办"
        assert all(cleared[key] is None for key in ("status", "status_as_of", "resolved_at", "resolved_note"))
        assert len(store.list_memory_history(memory_id)) == 5
        assert client.post(endpoint, json={"status": "invalid"}).status_code == 422
        assert client.post(endpoint, json={"status": "done", "note": "长" * 101}).status_code == 422
        assert client.post(endpoint, json={"note": "缺状态"}).status_code == 422
        assert client.post("/memories/999/status", json={"status": "open"}).status_code == 404
        page = client.get("/catalog").text
        for label in ("待续", "标完结", "标不做了", "重新打开", "这不是待办"):
            assert label in page
