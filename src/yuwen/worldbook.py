"""只读加载角色 prompt 与世界书。"""

from __future__ import annotations

from pathlib import Path

from yuwen.config import CharacterConfig


class WorldbookError(RuntimeError):
    """角色 prompt 或世界书无法读取。"""


class WorldbookReader:
    def __init__(self, config: CharacterConfig):
        self.config = config
        self._signature: tuple[tuple[str, int, int], ...] | None = None
        self._content: str | None = None

    def _paths(self) -> list[Path]:
        prompt = self.config.prompt_file
        if not prompt.is_file():
            raise WorldbookError(f"找不到角色 prompt：{prompt}")
        books = (
            sorted(self.config.worldbook_dir.glob("*.md"))
            if self.config.worldbook_dir.is_dir()
            else []
        )
        prompt_resolved = prompt.resolve()
        return [prompt, *(path for path in books if path.resolve() != prompt_resolved)]

    def read(self) -> str:
        """按文件名顺序拼接，并在任何文件变化后刷新缓存。"""

        paths = self._paths()
        signature = tuple(
            (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size)
            for path in paths
        )
        if signature == self._signature and self._content is not None:
            return self._content
        try:
            parts = [path.read_text(encoding="utf-8").strip() for path in paths]
        except OSError as exc:
            raise WorldbookError(f"读取世界书失败：{exc}") from exc
        self._signature = signature
        self._content = "\n\n".join(part for part in parts if part)
        return self._content
