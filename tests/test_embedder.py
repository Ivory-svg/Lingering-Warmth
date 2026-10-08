import asyncio
from dataclasses import replace
from pathlib import Path
import time

from fastapi.testclient import TestClient
import numpy as np

from test_chat_api import make_parts
from test_inflow import BatchLLM
from yuwen.api import create_app
from yuwen.config import EmbeddingConfig
from yuwen.embedder import MemoryEmbedder
from yuwen.store import Store


class FakeEmbedding:
    def __init__(self, *, fail: bool = False):
        self.calls: list[list[str]] = []
        self.fail = fail

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("假模型失败")
        return [[float(index + 1), 0.5] for index in range(len(texts))]


def add_memory(store: Store, session_id: int, summary: str, quote: str | None = None) -> int:
    return store.add_memory(
        kind="turn",
        happened_at="2026-10-07T10:00:00+08:00",
        summary=summary,
        quote=quote,
        emotion_text="平静",
        certainty=0.9,
        source_message_ids=[],
        session_id=session_id,
    )


def test_embedder_writes_float32_little_endian_and_uses_summary_quote(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    memory_id = add_memory(store, store.create_session(), "下雨了", "没带伞")
    fake = FakeEmbedding()
    embedder = MemoryEmbedder(store, fake, EmbeddingConfig("https://fake.test", model="bge-m3"))

    assert asyncio.run(embedder.embed_memory(memory_id))

    row = store.get_memory(memory_id)
    assert fake.calls == [["下雨了\n没带伞"]]
    assert row["embedding_model"] == "bge-m3"  # type: ignore[index]
    assert np.frombuffer(row["embedding"], dtype="<f4").tolist() == [1.0, 0.5]  # type: ignore[index]


def test_embedder_backfills_missing_and_old_model_in_batches(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    current_id = add_memory(store, session_id, "已经算过")
    missing_id = add_memory(store, session_id, "还没算")
    old_id = add_memory(store, session_id, "换模型")
    store.update_memory_embedding(current_id, np.array([1, 0], dtype="<f4").tobytes(), "new")
    store.update_memory_embedding(old_id, np.array([0, 1], dtype="<f4").tobytes(), "old")
    fake = FakeEmbedding()
    embedder = MemoryEmbedder(
        store, fake, EmbeddingConfig("https://fake.test", model="new", batch_size=1)
    )

    assert asyncio.run(embedder.backfill()) == 2
    assert fake.calls == [["还没算"], ["换模型"]]
    assert store.get_memory(missing_id)["embedding_model"] == "new"  # type: ignore[index]
    assert store.get_memory(old_id)["embedding_model"] == "new"  # type: ignore[index]
    assert asyncio.run(embedder.backfill()) == 0


def test_embedding_failure_keeps_memory_for_later_backfill(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    memory_id = add_memory(store, store.create_session(), "记得这件事")
    embedder = MemoryEmbedder(
        store, FakeEmbedding(fail=True), EmbeddingConfig("https://fake.test")
    )

    assert not asyncio.run(embedder.embed_memory(memory_id))
    assert store.get_memory(memory_id)["embedding"] is None  # type: ignore[index]
    assert asyncio.run(embedder.backfill()) == 0


def test_chat_background_embeds_handoff_and_inflow_memories(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    config = replace(
        config,
        llm=replace(config.llm, context_limit=1),
        gatekeeper=replace(config.gatekeeper, pre_send=False),
        embedding=EmbeddingConfig("https://fake.test", model="test-model"),
    )
    fake = FakeEmbedding()
    app = create_app(config, store=store, llm=BatchLLM(), worldbook=books, embedding_client=fake)

    with TestClient(app) as client:
        response = client.post("/chat", json={"content": "今晚吃什么"})

    assert response.status_code == 200
    assert response.json()["handoff_happened"] is True
    memories = store.list_table("memories")
    assert {row["kind"] for row in memories} == {"handoff", "turn"}
    assert all(row["embedding_model"] == "test-model" for row in memories)
    assert len(fake.calls) == 2


def test_startup_backfills_memory_from_previous_model(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    session_id = store.create_session()
    memory_id = add_memory(store, session_id, "以前的记忆")
    store.update_memory_embedding(memory_id, np.array([0, 1], dtype="<f4").tobytes(), "old")
    config = replace(config, embedding=EmbeddingConfig("https://fake.test", model="new"))
    fake = FakeEmbedding()
    app = create_app(config, store=store, llm=BatchLLM(), worldbook=books, embedding_client=fake)

    with TestClient(app):
        deadline = time.monotonic() + 2
        while store.get_memory(memory_id)["embedding_model"] != "new":  # type: ignore[index]
            assert time.monotonic() < deadline
            time.sleep(0.01)

    assert fake.calls == [["以前的记忆"]]
