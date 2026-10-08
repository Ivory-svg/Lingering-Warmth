"""后台给新记忆和旧记忆计算向量。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol, Sequence

import numpy as np

from yuwen.config import EmbeddingConfig
from yuwen.store import Store


logger = logging.getLogger(__name__)


class EmbeddingModel(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class MemoryEmbedder:
    def __init__(self, store: Store, client: EmbeddingModel, config: EmbeddingConfig):
        self.store = store
        self.client = client
        self.config = config

    @staticmethod
    def _text(memory: dict[str, Any]) -> str:
        summary = str(memory["summary"])
        quote = memory["quote"]
        return f"{summary}\n{quote}" if quote else summary

    async def _embed_rows(self, rows: list[dict[str, Any]]) -> int:
        if not rows or not self.config.base_url:
            return 0
        try:
            vectors = await self.client.embed([self._text(row) for row in rows])
            if len(vectors) != len(rows):
                raise ValueError("向量数量与记忆数量不一致")
            arrays = [np.asarray(vector, dtype="<f4") for vector in vectors]
            if any(array.ndim != 1 or not array.size or not np.isfinite(array).all() for array in arrays):
                raise ValueError("向量内容无效")
            if len({array.size for array in arrays}) != 1:
                raise ValueError("向量维度不一致")
        except Exception as exc:
            logger.warning("记忆向量计算失败，保留原记忆等待下次补算：%s", exc)
            return 0
        for row, array in zip(rows, arrays):
            self.store.update_memory_embedding(
                int(row["id"]), array.tobytes(), self.config.model
            )
        return len(rows)

    async def embed_memory(self, memory_id: int) -> bool:
        memory = self.store.get_memory(memory_id)
        return bool(memory and await self._embed_rows([memory]))

    async def backfill(self) -> int:
        """启动后按批补算；一批失败就留待下次启动重试。"""

        if not self.config.base_url:
            return 0
        total = 0
        after_id = 0
        while rows := self.store.list_memories_needing_embedding(
            self.config.model, after_id=after_id, limit=self.config.batch_size
        ):
            written = await self._embed_rows(rows)
            if not written:
                return total
            total += written
            after_id = int(rows[-1]["id"])
            await asyncio.sleep(0)
        return total
