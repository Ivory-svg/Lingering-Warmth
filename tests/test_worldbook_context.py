import os
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from test_chat_api import FakeLLM, make_parts
from yuwen.api import create_app
from yuwen.config import CharacterConfig, WorldbookConfig
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


def test_worldbook_matches_entries_by_keys_and_priority_with_budget(tmp_path: Path) -> None:
    directory = tmp_path / "worldbook"
    entries = directory / "entries"
    entries.mkdir(parents=True)
    (directory / "core.md").write_text("角色核心", encoding="utf-8")
    (entries / "father.md").write_text(
        "---\nkeys: 爸爸，父亲\npriority: 5\n---\n父亲是钟表匠", encoding="utf-8"
    )
    (entries / "shop.md").write_text(
        "---\nkeys: 钟表店\npriority: 1\n---\n他的店在街角", encoding="utf-8"
    )
    (entries / "missing.md").write_text("---\npriority: 9\n---\n不能带", encoding="utf-8")
    reader = WorldbookReader(CharacterConfig(directory / "core.md", directory))

    assert reader.core() == "角色核心"
    assert [entry.name for entry in reader.match_entries(["你爸爸的钟表店呢？"])] == [
        "father", "shop"
    ]
    assert [entry.name for entry in reader.match_entries(
        ["你爸爸的钟表店呢？"], entry_budget=len("父亲是钟表匠")
    )] == ["father"]
    assert reader.match_entries(["今天天气好"]) == []


def test_worldbook_warns_at_startup_for_entry_that_never_fits_budget(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config, store, _ = make_parts(tmp_path)
    directory = tmp_path / "worldbook"
    entries = directory / "entries"
    entries.mkdir(parents=True)
    (directory / "core.md").write_text("角色核心", encoding="utf-8")
    (entries / "long.md").write_text(
        "---\nkeys: 爸爸\npriority: 9\n---\n这条内容超过预算",
        encoding="utf-8",
    )
    (entries / "short.md").write_text(
        "---\nkeys: 爸爸\npriority: 1\n---\n短词条",
        encoding="utf-8",
    )
    config = replace(
        config,
        character=CharacterConfig(directory / "core.md", directory),
        worldbook=WorldbookConfig(entry_budget=4),
    )
    reader = WorldbookReader(config.character)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=reader)

    with caplog.at_level("WARNING"), TestClient(app):
        pass

    assert "long" in caplog.text
    assert "超过 entry_budget=4" in caplog.text
    assert "请拆短词条" in caplog.text
    assert "short" not in caplog.text
    assert [entry.name for entry in reader.match_entries(
        ["你爸爸呢？"], entry_budget=config.worldbook.entry_budget
    )] == ["short"]


def test_worldbook_samples_corpus_and_ignores_comments(tmp_path: Path) -> None:
    directory = tmp_path / "worldbook"
    directory.mkdir()
    (directory / "core.md").write_text("角色核心", encoding="utf-8")
    (directory / "corpus.md").write_text("# 注释\n第一句\n\n第二句\n", encoding="utf-8")
    reader = WorldbookReader(CharacterConfig(directory / "core.md", directory))

    assert set(reader.sample_corpus(8)) == {"第一句", "第二句"}
    assert len(reader.sample_corpus(1)) == 1
    assert reader.sample_corpus(0) == []


def test_legacy_worldbook_keeps_root_files_and_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    reader = make_reader(tmp_path)
    directory = tmp_path / "worldbook"
    (directory / "daily.md").write_text("第三档才会用的日常", encoding="utf-8")
    (directory / "corpus.md").write_text("语料不应整本带入", encoding="utf-8")

    with caplog.at_level("WARNING"):
        content = reader.core()

    assert content == "角色设定\n\n第一份\n\n第二份"
    assert "旧格式" in caplog.text


def test_worldbook_refreshes_entries_and_corpus_after_file_change(tmp_path: Path) -> None:
    directory = tmp_path / "worldbook"
    entries = directory / "entries"
    entries.mkdir(parents=True)
    (directory / "core.md").write_text("核心", encoding="utf-8")
    corpus_path = directory / "corpus.md"
    corpus_path.write_text("旧语料", encoding="utf-8")
    entry_path = entries / "father.md"
    entry_path.write_text("---\nkeys: FATHER\n---\n旧词条", encoding="utf-8")
    reader = WorldbookReader(CharacterConfig(directory / "core.md", directory))
    assert reader.match_entries(["father"])[0].content == "旧词条"
    assert reader.sample_corpus() == ["旧语料"]

    for path, content in [
        (entry_path, "---\nkeys: FATHER\n---\n新词条"),
        (corpus_path, "新语料"),
    ]:
        path.write_text(content, encoding="utf-8")
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000))

    assert reader.match_entries(["father"])[0].content == "新词条"
    assert reader.sample_corpus() == ["新语料"]


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


def test_context_uses_core_matched_entries_and_sampled_corpus(tmp_path: Path) -> None:
    directory = tmp_path / "worldbook"
    entries = directory / "entries"
    entries.mkdir(parents=True)
    (directory / "core.md").write_text("角色核心", encoding="utf-8")
    (directory / "corpus.md").write_text("第一句\n第二句", encoding="utf-8")
    (entries / "father.md").write_text(
        "---\nkeys: 爸爸\npriority: 5\n---\n父亲是钟表匠", encoding="utf-8"
    )
    reader = WorldbookReader(CharacterConfig(directory / "core.md", directory))
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    store.add_message(session_id, "user", "你爸爸呢？")

    messages = ContextBuilder(store, reader).build(session_id)

    assert messages[0] == {"role": "system", "content": "角色核心"}
    assert messages[1] == {"role": "system", "content": "词条：father\n父亲是钟表匠"}
    assert "不要原句复述" in messages[2]["content"]
    assert "第一句" in messages[2]["content"]
    assert messages[-1] == {"role": "user", "content": "你爸爸呢？"}


def test_context_entry_scan_depth_uses_recent_raw_messages(tmp_path: Path) -> None:
    directory = tmp_path / "worldbook"
    entries = directory / "entries"
    entries.mkdir(parents=True)
    (directory / "core.md").write_text("核心", encoding="utf-8")
    (entries / "father.md").write_text("---\nkeys: 爸爸\n---\n父亲词条", encoding="utf-8")
    reader = WorldbookReader(CharacterConfig(directory / "core.md", directory))
    store = Store(tmp_path / "test.db")
    store.initialize()
    session_id = store.create_session()
    store.add_message(session_id, "user", "爸爸在哪里？")
    store.add_message(session_id, "assistant", "先吃饭。")

    shallow = ContextBuilder(store, reader, scan_depth=1).build(session_id)
    deep = ContextBuilder(store, reader, scan_depth=2).build(session_id)

    assert not any("父亲词条" in item["content"] for item in shallow)
    assert any("父亲词条" in item["content"] for item in deep)
