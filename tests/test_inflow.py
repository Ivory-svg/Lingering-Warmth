import asyncio
from pathlib import Path

from yuwen.inflow import InflowService
from yuwen.store import Store


class ReplyLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.options: list[tuple[bool, bool]] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str:
        self.options.append((small, json_response))
        return self.reply


def turn(store: Store) -> tuple[int, int]:
    session_id = store.create_session()
    user_id = store.add_message(session_id, "user", "今天加班好累")
    assistant_id = store.add_message(session_id, "assistant", "先去吃饭，我在。")
    return user_id, assistant_id


def test_inflow_writes_valid_model_result(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    user_id, assistant_id = turn(store)
    llm = ReplyLLM(
        '{"summary":"她加班后很累，他让她先吃饭。","quote":"今天加班好累",'
        '"emotion_text":"疲惫，得到关心","valence":0.4,"arousal":0.3,"certainty":0.9}'
    )

    memory_id = asyncio.run(InflowService(store, llm).process_turn(user_id, assistant_id))
    memory = store.get_memory(memory_id)

    assert memory["kind"] == "turn"  # type: ignore[index]
    assert memory["certainty"] == 0.9  # type: ignore[index]
    assert llm.options == [(True, True)]


def test_inflow_garbled_result_uses_fallback_without_raising(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    user_id, assistant_id = turn(store)

    memory_id = asyncio.run(
        InflowService(store, ReplyLLM("这不是 JSON")).process_turn(user_id, assistant_id)
    )
    memory = store.get_memory(memory_id)

    assert memory["summary"] == "今天加班好累"  # type: ignore[index]
    assert memory["emotion_text"] == "未标注"  # type: ignore[index]
    assert memory["certainty"] == 0.5  # type: ignore[index]
