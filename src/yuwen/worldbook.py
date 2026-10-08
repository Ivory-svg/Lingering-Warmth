"""只读加载世界书核心、词条和语料，并兼容旧目录。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
import random
import re

from yuwen.config import CharacterConfig


logger = logging.getLogger(__name__)


class WorldbookError(RuntimeError):
    """世界书无法读取或格式不正确。"""


@dataclass(frozen=True, slots=True)
class WorldbookEntry:
    name: str
    content: str
    keys: tuple[str, ...]
    priority: int


class WorldbookReader:
    def __init__(self, config: CharacterConfig):
        self.config = config
        self._signature: tuple[tuple[str, int, int], ...] | None = None
        self._core: str = ""
        self._entries: tuple[WorldbookEntry, ...] = ()
        self._corpus: tuple[str, ...] = ()

    def _paths(self) -> tuple[Path, list[Path], Path | None, list[Path], bool]:
        directory = self.config.worldbook_dir
        core = directory / "core.md"
        modern = core.is_file()
        if not modern:
            core = self.config.prompt_file
            if not core.is_file():
                raise WorldbookError(f"找不到世界书核心：{core}")
        entry_paths = sorted((directory / "entries").glob("*.md"))
        corpus = directory / "corpus.md"
        corpus_path = corpus if corpus.is_file() else None
        legacy_paths = []
        if not modern and directory.is_dir():
            legacy_paths = [
                path
                for path in sorted(directory.glob("*.md"))
                if path.resolve() != core.resolve()
                and path.name not in {"corpus.md", "daily.md"}
            ]
        return core, entry_paths, corpus_path, legacy_paths, modern

    @staticmethod
    def _parse_entry(path: Path) -> WorldbookEntry | None:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        fields: dict[str, str] = {}
        body = text.strip()
        if lines and lines[0].strip() == "---":
            try:
                closing = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
            except StopIteration:
                closing = len(lines)
            for line in lines[1:closing]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    fields[key.strip()] = value.strip()
            body = "\n".join(lines[closing + 1 :]).strip()
        keys = tuple(
            key.strip().casefold()
            for key in re.split(r"[,，]", fields.get("keys", ""))
            if key.strip()
        )
        if not keys:
            logger.warning("世界书词条缺少 keys，已跳过：%s", path)
            return None
        try:
            priority = int(fields.get("priority", "0"))
        except ValueError as exc:
            raise WorldbookError(f"世界书词条 priority 不是整数：{path}") from exc
        return WorldbookEntry(path.stem, body, keys, priority)

    def _load(self) -> None:
        core, entry_paths, corpus_path, legacy_paths, modern = self._paths()
        paths = [core, *entry_paths, *legacy_paths]
        if corpus_path is not None:
            paths.append(corpus_path)
        try:
            signature = tuple(
                (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
                for path in paths
            )
            if signature == self._signature:
                return
            core_text = core.read_text(encoding="utf-8").strip()
            if not modern:
                logger.warning("世界书使用旧格式，建议拆分为 core.md、entries/ 和 corpus.md")
                legacy_parts = []
                for path in legacy_paths:
                    text = path.read_text(encoding="utf-8").strip()
                    if not text.startswith("---\n"):
                        legacy_parts.append(text)
                core_text = "\n\n".join(part for part in [core_text, *legacy_parts] if part)
            entries = tuple(
                entry
                for path in entry_paths
                if (entry := self._parse_entry(path)) is not None
            )
            corpus = ()
            if corpus_path is not None:
                corpus = tuple(
                    line.strip()
                    for line in corpus_path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                )
        except OSError as exc:
            raise WorldbookError(f"读取世界书失败：{exc}") from exc
        self._signature = signature
        self._core = core_text
        self._entries = entries
        self._corpus = corpus

    def core(self) -> str:
        self._load()
        return self._core

    def read(self) -> str:
        """兼容旧调用；返回每轮都要带入的核心内容。"""

        return self.core()

    def match_entries(
        self,
        recent: Sequence[str],
        *,
        scan_depth: int = 4,
        entry_budget: int = 3000,
    ) -> list[WorldbookEntry]:
        self._load()
        haystack = "\n".join(recent[-scan_depth:]).casefold() if scan_depth > 0 else ""
        candidates = sorted(
            (
                entry
                for entry in self._entries
                if any(key in haystack for key in entry.keys)
            ),
            key=lambda entry: (-entry.priority, entry.name),
        )
        selected: list[WorldbookEntry] = []
        remaining = entry_budget
        for entry in candidates:
            if len(entry.content) <= remaining:
                selected.append(entry)
                remaining -= len(entry.content)
        return selected

    def sample_corpus(self, count: int = 8) -> list[str]:
        self._load()
        if count <= 0:
            return []
        return random.sample(self._corpus, min(count, len(self._corpus)))
