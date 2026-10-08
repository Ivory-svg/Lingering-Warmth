import asyncio
from datetime import datetime, timezone
from pathlib import Path

from dataclasses import replace
from fastapi.testclient import TestClient
import numpy as np

from test_chat_api import FakeLLM, make_parts
from test_handoff import SequenceLLM
from yuwen.api import create_app
from yuwen.chat import ChatService
from yuwen.config import DecayConfig, EmbeddingConfig, SurfacingConfig
from yuwen.context import ContextBuilder
from yuwen.handoff import HandoffService
from yuwen.llm import estimate_tokens
from yuwen.store import Store
from yuwen.surfacing import SurfacingService


NOW = datetime(2026, 10, 8, tzinfo=timezone.utc)


class FakeEmbedding:
    def __init__(self, vector: list[float] | None = None, *, fail: bool = False):
        self.vector = vector or [1.0, 0.0]
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("假向量接口失败")
        return [self.vector for _ in texts]


def setup_store(tmp_path: Path) -> tuple[Store, int]:
    store = Store(tmp_path / "test.db")
    store.initialize()
    return store, store.create_session()


def memory(
    store: Store,
    session_id: int,
    summary: str,
    *,
    happened_at: str = "2026-10-07T00:00:00+00:00",
    source_ids: list[int] | None = None,
    sealed: bool = False,
    kind: str = "turn",
    certainty: float = 0.9,
    quote: str | None = None,
    vector: list[float] | None = None,
) -> int:
    memory_id = store.add_memory(
        kind=kind,
        happened_at=happened_at,
        summary=summary,
        quote=quote,
        emotion_text="平静",
        certainty=certainty,
        source_message_ids=source_ids or [],
        session_id=session_id,
        sealed=sealed,
    )
    if vector is not None:
        store.update_memory_embedding(memory_id, np.asarray(vector, dtype="<f4").tobytes(), "bge-m3")
    return memory_id


def service(
    store: Store,
    *,
    client: FakeEmbedding | None = None,
    config: SurfacingConfig = SurfacingConfig(),
) -> SurfacingService:
    return SurfacingService(
        store,
        config=config,
        decay=DecayConfig(7),
        embedding=EmbeddingConfig(base_url="https://fake.test") if client else EmbeddingConfig(),
        client=client,
        clock=lambda: NOW,
    )


def test_semantic_search_orders_matches_and_uses_recent_reply(tmp_path: Path) -> None:
    store, session_id = setup_store(tmp_path)
    best = memory(store, session_id, "她淋雨后没带伞", vector=[1, 0], certainty=0.5)
    memory(store, session_id, "买了新雨伞", vector=[0.8, 0.6])
    memory(store, session_id, "完全无关", vector=[0, 1])
    store.add_message(session_id, "assistant", "上次下雨你没带伞。")
    store.add_message(session_id, "user", "那次淋雨呢？")
    fake = FakeEmbedding()

    result = asyncio.run(service(store, client=fake).recall(session_id, "那次淋雨呢？"))

    assert result is not None
    assert result.index("淋雨后没带伞") < result.index("买了新雨伞")
    assert "完全无关" not in result
    assert "她淋雨后没带伞" in result
    assert "小雨淋雨后没带伞" not in result
    assert "10月7日（记不太清）" in result
    assert fake.calls == [["上次下雨你没带伞。\n那次淋雨呢？"]]
    assert store.get_memory(best)["recall_count"] == 1  # type: ignore[index]


def test_surfacing_keeps_old_summary_pronouns_verbatim(tmp_path: Path) -> None:
    store, session_id = setup_store(tmp_path)
    memory(store, session_id, "她说下雨了，他帮她打伞")

    result = asyncio.run(service(store).recall(session_id, "下雨了打伞"))

    assert result is not None
    assert "她说下雨了，他帮她打伞" in result
    assert "小雨" not in result


def test_literal_fallback_excludes_sealed_dream_and_visible_sources(tmp_path: Path) -> None:
    store, session_id = setup_store(tmp_path)
    old_session = store.create_session()
    old_id = store.add_message(old_session, "user", "以前下雨没带伞")
    visible_id = store.add_message(session_id, "user", "现在下雨没带伞")
    kept = memory(store, old_session, "下雨没带伞", source_ids=[old_id])
    sealed = memory(store, old_session, "下雨没带伞的秘密", sealed=True)
    dream = memory(store, old_session, "梦见下雨没带伞", kind="dream")
    visible = memory(store, session_id, "刚说下雨没带伞", source_ids=[visible_id])

    result = asyncio.run(service(store).recall(session_id, "下雨没带伞"))

    assert result is not None and "下雨没带伞" in result
    assert store.get_memory(kept)["recall_count"] == 1  # type: ignore[index]
    assert all(store.get_memory(item)["recall_count"] == 0 for item in (sealed, dream, visible))  # type: ignore[index]


def test_handoff_tail_sources_are_also_visible(tmp_path: Path) -> None:
    store, old_session = setup_store(tmp_path)
    old_id = store.add_message(old_session, "user", "以前下雨没带伞")
    covered = memory(store, old_session, "下雨没带伞", source_ids=[old_id])
    handoff_id = store.add_handoff(
        session_id=old_session,
        doing="聊天",
        where_we_are="说起下雨",
        emotion="平静",
        raw_tail_message_ids=[old_id],
    )
    new_session = store.create_session(continues_handoff_id=handoff_id)

    assert asyncio.run(service(store).recall(new_session, "下雨没带伞")) is None
    assert store.get_memory(covered)["recall_count"] == 0  # type: ignore[index]


def test_decay_moves_old_memory_back_without_removing_it_and_recall_reorders(tmp_path: Path) -> None:
    store, session_id = setup_store(tmp_path)
    old = memory(
        store, session_id, "很久以前的雨伞", happened_at="2025-10-07T00:00:00+00:00",
        vector=[1, 0],
    )
    recent = memory(store, session_id, "昨天的雨伞", vector=[0.8, 0.6])
    finder = service(store, client=FakeEmbedding())

    first = asyncio.run(finder.recall(session_id, "雨伞"))
    second = asyncio.run(finder.recall(session_id, "雨伞"))

    assert first is not None and second is not None
    assert first.index("昨天的雨伞") < first.index("很久以前的雨伞")
    assert second.index("很久以前的雨伞") < second.index("昨天的雨伞")
    assert "2025年10月7日" in first
    assert store.get_memory(old)["recall_count"] == 2  # type: ignore[index]
    assert store.get_memory(recent)["recall_count"] == 2  # type: ignore[index]
    assert store.get_memory(old)["last_recalled_at"] == NOW.isoformat()  # type: ignore[index]


def test_embedding_failure_uses_literal_matching(tmp_path: Path) -> None:
    store, session_id = setup_store(tmp_path)
    matched = memory(store, session_id, "下雨没带伞", vector=[1, 0])
    fake = FakeEmbedding(fail=True)

    result = asyncio.run(service(store, client=fake).recall(session_id, "下雨没带伞"))

    assert result is not None and "下雨没带伞" in result
    assert fake.calls == [["下雨没带伞"]]
    assert store.get_memory(matched)["recall_count"] == 1  # type: ignore[index]


def test_invalid_stored_vector_uses_literal_matching(tmp_path: Path) -> None:
    store, session_id = setup_store(tmp_path)
    matched = memory(store, session_id, "下雨没带伞")
    store.update_memory_embedding(matched, b"bad", "bge-m3")

    result = asyncio.run(
        service(store, client=FakeEmbedding()).recall(session_id, "下雨没带伞")
    )

    assert result is not None and "下雨没带伞" in result
    assert store.get_memory(matched)["recall_count"] == 1  # type: ignore[index]


def test_surfacing_respects_count_budget_and_disabled_flag(tmp_path: Path) -> None:
    store, session_id = setup_store(tmp_path)
    first = memory(store, session_id, "下雨没带伞", vector=[1, 0])
    second = memory(store, session_id, "下雨没带伞，后来买了伞", vector=[1, 0])
    fake = FakeEmbedding()
    limited = service(store, client=fake, config=SurfacingConfig(top_k=1))

    result = asyncio.run(limited.recall(session_id, "下雨没带伞"))

    assert result is not None
    assert sum(store.get_memory(item)["recall_count"] for item in (first, second)) == 1  # type: ignore[index]
    disabled = service(store, client=fake, config=SurfacingConfig(enabled=False))
    assert asyncio.run(disabled.recall(session_id, "下雨没带伞")) is None
    assert len(fake.calls) == 1


def test_surfacing_skips_memory_that_exceeds_character_budget(tmp_path: Path) -> None:
    store, session_id = setup_store(tmp_path)
    short = memory(store, session_id, "雨伞", vector=[1, 0])
    long = memory(store, session_id, "很长的雨伞" * 80, vector=[1, 0])
    finder = service(store, client=FakeEmbedding(), config=SurfacingConfig(max_chars=180))

    result = asyncio.run(finder.recall(session_id, "雨伞"))

    assert result is not None and len(result) <= 180
    assert store.get_memory(short)["recall_count"] == 1  # type: ignore[index]
    assert store.get_memory(long)["recall_count"] == 0  # type: ignore[index]


def test_chat_context_includes_recalled_memory(tmp_path: Path) -> None:
    config, store, books = make_parts(tmp_path)
    config = replace(config, gatekeeper=replace(config.gatekeeper, pre_send=False))
    old_session = store.create_session()
    old_memory = memory(store, old_session, "下雨没带伞")
    store.close_session(old_session)
    llm = FakeLLM()
    app = create_app(config, store=store, llm=llm, worldbook=books)

    with TestClient(app) as client:
        response = client.post("/chat", json={"content": "下雨没带伞"})

    assert response.status_code == 200
    assert any("下雨没带伞" in item["content"] and "往事" in item["content"]
               for item in llm.calls[0])
    assert store.get_memory(old_memory)["recall_count"] == 1  # type: ignore[index]


def test_handoff_threshold_includes_recall_and_counts_it_once(tmp_path: Path) -> None:
    _, store, books = make_parts(tmp_path)
    session_id = store.create_session()
    old_memory = memory(store, session_id, "雨伞")
    context = ContextBuilder(store, books)
    without_recall = estimate_tokens(
        "\n".join(item["content"] for item in context.build(session_id))
    )
    llm = SequenceLLM([
        '{"doing":"聊天","where_we_are":"说起雨伞","emotion":"平静"}',
        "把伞带上。",
    ])
    handoff = HandoffService(
        store, llm, context_limit=without_recall + 30, trigger_ratio=1, raw_tail=20
    )
    chat = ChatService(store, context, llm, handoff=handoff, surfacing=service(store))

    result = asyncio.run(chat.handle_message("雨伞"))

    assert result.handoff_happened
    assert "自然想起来的往事" in str(llm.calls[0][0])
    assert "自然想起来的往事" in str(llm.calls[1][0])
    assert store.get_memory(old_memory)["recall_count"] == 1  # type: ignore[index]
