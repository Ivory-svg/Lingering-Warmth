"""不用 API Key 就能打开网页的本机演示；模型回复只用固定规则模拟。"""

from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
from threading import Timer
import webbrowser

import uvicorn
from fastapi import FastAPI

from yuwen.api import create_app
from yuwen.config import (
    AppConfig,
    AuthConfig,
    CharacterConfig,
    GatekeeperConfig,
    HandoffConfig,
    LLMConfig,
    ServerConfig,
    StorageConfig,
    WakeConfig,
)


class DemoModel:
    """只演示后端的写入、翻阅与封页流程，不模拟真实模型判断力。"""

    async def chat(
        self, messages: list[dict[str, str]], *, small: bool = False, json_response: bool = False
    ) -> str:
        if small:
            return self._inflow(messages[0]["content"])
        if json_response:
            return json.dumps(
                {"doing": "继续聊天", "where_we_are": "刚说到近况", "emotion": "平静"},
                ensure_ascii=False,
            )
        user = next((row["content"] for row in reversed(messages) if row["role"] == "user"), "")
        if re.search(r"忘掉|别记|当我没说|别再提", user):
            return "（演示回复）好，不提了。"
        remembered = next(
            (
                row["content"]
                for row in messages
                if row["role"] == "system" and "这些是你此刻自然想起来的往事" in row["content"]
            ),
            "",
        )
        if remembered:
            line = next((line for line in remembered.splitlines() if line.startswith("· ")), "")
            detail = line.split("：", 1)[-1].split("。当时", 1)[0]
            return f"（演示回复）我记得，{detail}。"
        if "生日" in user or "纪念日" in user:
            return "（演示回复）嗯，这一天我记下了。"
        return "（演示回复）嗯，我听着。"

    @staticmethod
    def _inflow(prompt: str) -> str:
        dialogue = prompt.split("【对话】\n", 1)[-1].split("\n【时间】", 1)[0]
        user_lines = re.findall(r"^对方：(.*)$", dialogue, flags=re.MULTILINE)
        assistant_lines = re.findall(r"^沈砚：(.*)$", dialogue, flags=re.MULTILINE)
        latest = user_lines[-1] if user_lines else "聊了几句"
        reply = assistant_lines[-1] if assistant_lines else "听着"
        surfaced = prompt.split("【这几轮里递给沈砚的往事】\n", 1)
        surfaced_text = surfaced[1].split("\n【", 1)[0] if len(surfaced) > 1 else ""
        surfaced_ids = re.findall(r"\[#(\d+)\]", surfaced_text)
        result: dict[str, object] = {
            "summary": f"对方说「{latest[:100]}」，沈砚回答「{reply[:100]}」",
            "quote": latest[:100],
            "emotion_text": "平静，认真听着",
            "valence": 0.5,
            "arousal": 0.3,
            "certainty": 0.9,
            "used_memory_ids": [int(surfaced_ids[0])] if surfaced_ids and "我记得" in reply else [],
            "todos": [],
            "resolved": [],
            "forget": None,
            "anniversaries": [],
        }
        if re.search(r"忘掉|别记|当我没说|别再提", latest):
            result["forget"] = {
                "this_batch": True,
                "ids": [],
                "reason": "对方说不想再提这件事",
            }
        date = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日", latest)
        if date and ("生日" in latest or "纪念日" in latest):
            year, month, day = date.groups()
            result["anniversaries"] = [
                {
                    "date": f"{year + '-' if year else ''}{int(month):02d}-{int(day):02d}",
                    "title": "对方的生日" if "生日" in latest else "纪念日",
                    "yearly": True,
                }
            ]
        return json.dumps(result, ensure_ascii=False)


def create_demo_app(db_path: Path | None = None) -> tuple[FastAPI, str]:
    root = Path(__file__).resolve().parents[2]
    worldbook = root / "examples" / "worldbook"
    password = secrets.token_urlsafe(12)
    config = AppConfig(
        llm=LLMConfig("https://example.invalid/v1", "demo", "demo", "demo", context_limit=1100),
        character=CharacterConfig(
            worldbook / "core.md", worldbook, name="沈砚", player_name="对方"
        ),
        handoff=HandoffConfig(trigger_ratio=0.6, raw_tail=0, min_turns=2),
        gatekeeper=GatekeeperConfig(pre_send=False),
        storage=StorageConfig(db_path or root / "data" / "demo.db"),
        server=ServerConfig(),
        config_path=root / "config.toml",
        wake=WakeConfig(enabled=False),
        auth=AuthConfig(password=password),
    )
    return create_app(config, llm=DemoModel()), password


def run() -> None:
    app, password = create_demo_app()
    url = "http://127.0.0.1:8787"
    print(f"余温离线演示：打开 {url}", flush=True)
    print(f"本次登录密码：{password}", flush=True)
    print("回复由固定规则模拟；记忆、目录、封页和纪年使用真实后端。按 Ctrl+C 停止。", flush=True)
    browser = Timer(1.0, webbrowser.open, args=(url,))
    browser.daemon = True
    browser.start()
    uvicorn.run(app, host="127.0.0.1", port=8787)
