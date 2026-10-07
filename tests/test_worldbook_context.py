import os
from pathlib import Path

from yuwen.config import CharacterConfig
from yuwen.context import ContextBuilder
from yuwen.store import Store
from yuwen.worldbook import WorldbookReader


def make_reader(tmp_path: Path) -> WorldbookReader:
    books = tmp_path / "worldbook"
    books.mkdir()
    (books / "character.md").write_text("角色设定", encoding="utf-8")
    (books / "b.md").write_text("第二份", encoding="utf-8")
    (books / "a.md").write_text("第一份", encoding="utf-8")
    return WorldbookReader(
        CharacterConfig(prompt_file=books / "character.md", worldbook_dir=books)
    )


def test_worldbook_prompt_first_then_sorted_books_without_duplicate(tmp_path: Path) -> None:
    content = make_reader(tmp_path).read()

    assert content == "角色设定\n\n第一份\n\n第二份"


def test_worldbook_refreshes_when_file_changes(tmp_path: Path) -> None:
    reader = make_reader(tmp_path)
    assert "第一份" in reader.read()
    path = tmp_path / "worldbook" / "a.md"
    path.write_text("改过的第一份", encoding="utf-8")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))

    assert "改过的第一份" in reader.read()


def test_context_order_includes_notes_handoff_tail_and_current_messages(tmp_path: Path) -> None:
    store = Store(tmp_path / "test.db")
    store.initialize()
    previous_id = store.create_session()
    old_user = store.add_message(previous_id, "user", "上一窗原话")
    old_assistant = store.add_message(previous_id, "assistant", "上一窗回答")
    handoff_id = store.add_handoff(
        session_id=previous_id,
        doing="等外卖",
        where_we_are="聊明天的面试",
        emotion="有点担心她",
        raw_tail_message_ids=[old_user, old_assistant],
    )
    store.close_session(previous_id)
    current_id = store.create_session(continues_handoff_id=handoff_id)
    store.add_message(current_id, "user", "我们继续吧")
    store.add_voice_note(
        source="player",
        original="加油！",
        rewritten="先坐好，我陪你过一遍。",
        reason="不要只说空话",
    )

    messages = ContextBuilder(store, make_reader(tmp_path)).build(current_id)

    assert [message["role"] for message in messages] == [
        "system",
        "system",
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert messages[0]["content"].startswith("角色设定")
    assert "语感笔记" in messages[1]["content"]
    assert "等外卖" in messages[2]["content"]
    assert messages[3]["content"] == "上一窗原话"
    assert messages[-1]["content"] == "我们继续吧"
