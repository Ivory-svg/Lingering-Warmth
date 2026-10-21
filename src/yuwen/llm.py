"""OpenAI 兼容模型客户端；项目内只有这个模块发外部网络请求。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from email.header import Header
import json
import math
from typing import Any

import httpx

from yuwen.config import EmbeddingConfig, LLMConfig, WakeConfig


class LLMError(RuntimeError):
    """模型调用失败。"""


class LLMResponseError(LLMError):
    """模型响应缺少所需内容或 JSON 格式错误。"""


Sleep = Callable[[float], Awaitable[None]]


def estimate_tokens(text: str) -> int:
    """按文档约定，用字符数除以 1.5 粗估 token。"""

    return math.ceil(len(text) / 1.5) if text else 0


class LLMClient:
    def __init__(
        self,
        config: LLMConfig,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ):
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=60)
        self._owns_client = client is None
        self._sleep = sleep

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request_text(
        self,
        messages: Sequence[dict[str, str]],
        *,
        small: bool,
        json_response: bool,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.config.small_model if small else self.config.model,
            "messages": list(messages),
        }
        if json_response:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.post(
                    f"{self.config.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content:
                    raise LLMResponseError("模型响应里没有文本内容")
                return content
            except (
                httpx.HTTPError,
                LLMResponseError,
                KeyError,
                IndexError,
                TypeError,
                ValueError,
            ) as exc:
                last_error = exc
                if attempt == 2:
                    break
                await self._sleep(2**attempt)
        raise LLMError("模型调用在重试后仍然失败") from last_error

    async def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        small: bool = False,
        json_response: bool = False,
    ) -> str:
        """生成回复；要求 JSON 时会验证格式，并在解析失败后重试一次。"""

        parse_error: Exception | None = None
        parse_attempts = 2 if json_response else 1
        for _ in range(parse_attempts):
            text = await self._request_text(
                messages,
                small=small,
                json_response=json_response,
            )
            if not json_response:
                return text
            try:
                parsed = json.loads(text)
                if not isinstance(parsed, dict):
                    raise ValueError("JSON 顶层必须是对象")
                return text
            except (json.JSONDecodeError, ValueError) as exc:
                parse_error = exc
        raise LLMResponseError("模型连续返回了无效 JSON") from parse_error


class EmbeddingClient:
    """用独立的 OpenAI 兼容地址批量生成记忆向量。"""

    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ):
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=60)
        self._owns_client = client is None
        self._sleep = sleep

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @staticmethod
    def _vectors(data: Any, count: int) -> list[list[float]]:
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != count:
            raise LLMResponseError("embedding 响应数量不匹配")
        vectors: list[list[float] | None] = [None] * count
        dimensions: int | None = None
        for item in items:
            if not isinstance(item, dict):
                raise LLMResponseError("embedding 响应条目无效")
            index = item.get("index")
            values = item.get("embedding")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or not 0 <= index < count
                or vectors[index] is not None
                or not isinstance(values, list)
                or not values
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    for value in values
                )
            ):
                raise LLMResponseError("embedding 响应向量无效")
            if dimensions is not None and len(values) != dimensions:
                raise LLMResponseError("embedding 向量维度不一致")
            dimensions = len(values)
            vectors[index] = [float(value) for value in values]
        if any(vector is None for vector in vectors):
            raise LLMResponseError("embedding 响应缺少条目")
        return [vector for vector in vectors if vector is not None]

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not self.config.base_url or not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.post(
                    f"{self.config.base_url.rstrip('/')}/embeddings",
                    headers=headers,
                    json={"model": self.config.model, "input": list(texts)},
                )
                response.raise_for_status()
                return self._vectors(response.json(), len(texts))
            except (httpx.HTTPError, LLMResponseError, ValueError, TypeError) as exc:
                last_error = exc
                if attempt == 2:
                    break
                await self._sleep(2**attempt)
        raise LLMError("embedding 调用在重试后仍然失败") from last_error


class WakeNotifier:
    """来信写入后，向玩家配置的地址发送一次通知。"""

    def __init__(self, config: WakeConfig, *, client: httpx.AsyncClient | None = None):
        self.config = config
        self._client = client or httpx.AsyncClient(timeout=5)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send(self, payload: dict[str, Any]) -> bool:
        if not self.config.notify_url:
            return False
        if self.config.notify_format == "json":
            response = await self._client.post(self.config.notify_url, json=payload, timeout=5)
        else:
            response = await self._client.post(
                self.config.notify_url,
                content=str(payload["content"]).encode("utf-8"),
                headers={"Title": Header(str(payload["character"]), "utf-8").encode(),
                         "Content-Type": "text/plain; charset=utf-8"},
                timeout=5,
            )
        return 200 <= response.status_code < 300
