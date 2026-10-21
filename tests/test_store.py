import json
from importlib.resources import files
from pathlib import Path
import sqlite3

from yuwen.store import Store


def make_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "data" / "test.db")
    store.initialize()
    return store


def test_initialize_is_repeatable_and_sets_schema_version(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.initialize()

    assert store.schema_version() == "6"
    assert store.path.exists()


def test_initialize_migrates_existing_v1_database_without_losing_notes(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            files("yuwen").joinpath("migrations", "001_initial.sql").read_text(encoding="utf-8")
        )
    store = Store(path)
    session_id = store.create_session()
    message_id = store.add_message(session_id, "assistant", "原回复")
    note_id = store.add_voice_note(
        source="gatekeeper",
        message_id=message_id,
        original="原句",
        rewritten="原回复",
        reason="太直白",
    )

    store.initialize()
    store.initialize()

    assert store.schema_version() == "6"
    assert store.get_message(message_id)["content"] == "原回复"  # type: ignore[index]
    note = store.list_voice_notes()[0]
    assert note["id"] == note_id
    assert note["overturned_at"] is None
    assert note["overturn_note"] is None


def test_initialize_migrates_v2_memories_without_losing_content(tmp_path: Path) -> None:
    path = tmp_path / "v2.db"
    with sqlite3.connect(path) as connection:
        for migration in ("001_initial.sql", "002_voice_notes_overturn.sql"):
            connection.executescript(
                files("yuwen").joinpath("migrations", migration).read_text(encoding="utf-8")
            )
    store = Store(path)
    session_id = store.create_session()
    memory_id = store.add_memory(
        kind="turn",
        happened_at="2026-10-07T10:00:00+08:00",
        summary="原有的记忆",
        emotion_text="平静",
        certainty=0.9,
        source_message_ids=[],
        session_id=session_id,
        embedding=b"old-vector",
    )

    store.initialize()
    store.initialize()

    memory = store.get_memory(memory_id)
    assert store.schema_version() == "6"
    assert memory["summary"] == "原有的记忆"  # type: ignore[index]
    assert memory["embedding"] == b"old-vector"  # type: ignore[index]
    assert memory["embedding_model"] is None  # type: ignore[index]
    assert memory["recall_count"] == 0  # type: ignore[index]


def test_initialize_migrates_v3_auth_sessions_without_losing_memories(tmp_path: Path) -> None:
    path = tmp_path / "v3.db"
    with sqlite3.connect(path) as connection:
        for migration in (
            "001_initial.sql", "002_voice_notes_overturn.sql", "003_memory_recall.sql"
        ):
            connection.executescript(
                files("yuwen").joinpath("migrations", migration).read_text(encoding="utf-8")
            )
    store = Store(path)
    memory_id = store.add_memory(
        kind="turn", happened_at="2026-10-07T10:00:00+08:00", summary="旧记忆",
        emotion_text="平静", certainty=0.9, source_message_ids=[],
        session_id=store.create_session(),
    )

    store.initialize()
    store.initialize()

    assert store.schema_version() == "6"
    assert store.get_memory(memory_id)["summary"] == "旧记忆"  # type: ignore[index]
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'auth_sessions'"
        ).fetchone() is not None


def test_initialize_migrates_v4_todo_fields_and_index(tmp_path: Path) -> None:
    path = tmp_path / "v4.db"
    with sqlite3.connect(path) as connection:
        for migration in (
            "001_initial.sql", "002_voice_notes_overturn.sql",
            "003_memory_recall.sql", "004_auth_sessions.sql",
        ):
            connection.executescript(
                files("yuwen").joinpath("migrations", migration).read_text(encoding="utf-8")
            )
    store = Store(path)
    memory_id = store.add_memory(
        kind="turn", happened_at="2026-10-20T10:00:00+08:00", summary="已有记忆",
        emotion_text="平静", certainty=0.9, source_message_ids=[],
        session_id=store.create_session(),
    )

    store.initialize()
    store.initialize()

    memory = store.get_memory(memory_id)
    assert store.schema_version() == "6"
    assert memory["summary"] == "已有记忆"  # type: ignore[index]
    assert all(memory[column] is None for column in (
        "status", "status_as_of", "resolved_at", "resolved_note"
    ))  # type: ignore[index]
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = 'memories_status_idx'"
        ).fetchone() is not None


def test_initialize_migrates_v5_wake_log(tmp_path: Path) -> None:
    path = tmp_path / "v5.db"
    with sqlite3.connect(path) as connection:
        for migration in (
            "001_initial.sql", "002_voice_notes_overturn.sql", "003_memory_recall.sql",
            "004_auth_sessions.sql", "005_todo_status.sql",
        ):
            connection.executescript(
                files("yuwen").joinpath("migrations", migration).read_text(encoding="utf-8")
            )
    store = Store(path)
    session_id = store.create_session()
    message_id = store.add_message(session_id, "user", "旧对话")

    store.initialize()
    store.initialize()

    assert store.schema_version() == "6"
    assert store.get_message(message_id)["content"] == "旧对话"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'wake_log'"
        ).fetchone() is not None


def test_session_and_message_round_trip(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    session_id = store.create_session(started_at="2026-10-07T10:00:00+08:00")
    first_id = store.add_message(
        session_id,
        "user",
        "今天好累",
        created_at="2026-10-07T10:01:00+08:00",
    )
    second_id = store.add_message(session_id, "assistant", "先去吃点东西。")

    assert store.get_message(first_id)["content"] == "今天好累"  # type: ignore[index]
    assert [row["id"] for row in store.list_tail_messages(session_id, 1)] == [second_id]

    store.close_session(session_id, ended_at="2026-10-07T10:02:00+08:00")
    assert store.get_session(session_id)["ended_at"] is not None  # type: ignore[index]


def test_memory_handoff_and_voice_note_round_trip(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    session_id = store.create_session()
    user_id = store.add_message(session_id, "user", "明天要面试")
    assistant_id = store.add_message(session_id, "assistant", "我陪你练一遍。")
    memory_id = store.add_memory(
        kind="turn",
        happened_at="2026-10-07T10:02:00+08:00",
        summary="她说明天要面试，他答应陪她练习。",
        quote="明天要面试",
        emotion_text="紧张，但得到支持",
        valence=0.6,
        arousal=0.5,
        certainty=0.9,
        source_message_ids=[user_id, assistant_id],
        session_id=session_id,
    )
    memory = store.get_memory(memory_id)
    assert json.loads(memory["source_message_ids"]) == [user_id, assistant_id]  # type: ignore[index]

    history_id = store.add_memory_history(memory_id, memory, reason="测试快照")  # type: ignore[arg-type]
    assert store.list_table("memory_history")[0]["id"] == history_id

    handoff_id = store.add_handoff(
        session_id=session_id,
        doing="准备面试",
        where_we_are="刚答应模拟",
        emotion="有点心疼",
        raw_tail_message_ids=[user_id, assistant_id],
    )
    new_session_id = store.create_session(continues_handoff_id=handoff_id)
    assert store.get_session(new_session_id)["continues_handoff_id"] == handoff_id  # type: ignore[index]

    note_id = store.add_voice_note(
        source="player",
        message_id=assistant_id,
        original="加油！",
        rewritten="先坐好，我陪你过一遍。",
        reason="他不会只说空泛的鼓励",
    )
    assert store.list_voice_notes(active_only=True)[0]["id"] == note_id
    assert store.update_voice_note_active(note_id, False)
    assert store.list_voice_notes(active_only=True) == []


def test_get_or_create_active_session_reuses_open_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.get_or_create_active_session()
    second = store.get_or_create_active_session()

    assert first["id"] == second["id"]
