"""读取并校验余温的 TOML 配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any


class ConfigError(ValueError):
    """配置缺失或格式不正确。"""


@dataclass(frozen=True, slots=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    small_model: str
    context_limit: int = 64_000


@dataclass(frozen=True, slots=True)
class CharacterConfig:
    prompt_file: Path
    worldbook_dir: Path


@dataclass(frozen=True, slots=True)
class HandoffConfig:
    trigger_ratio: float = 0.75
    raw_tail: int = 20


@dataclass(frozen=True, slots=True)
class GatekeeperConfig:
    pre_send: bool = True
    voice_notes_in_context: int = 10


@dataclass(frozen=True, slots=True)
class StorageConfig:
    db_path: Path


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8787


@dataclass(frozen=True, slots=True)
class AppConfig:
    llm: LLMConfig
    character: CharacterConfig
    handoff: HandoffConfig
    gatekeeper: GatekeeperConfig
    storage: StorageConfig
    server: ServerConfig
    config_path: Path


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"配置段 [{name}] 必须是表")
    return value


def _required_text(section: dict[str, Any], key: str, section_name: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"缺少配置 [{section_name}] {key}")
    return value.strip()


def _resolve_path(base: Path, value: object, default: str) -> Path:
    raw = value if isinstance(value, str) and value.strip() else default
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: str | Path = "config.toml") -> AppConfig:
    """从文件读取配置，并把相对路径解析为相对配置文件的位置。"""

    config_path = Path(path).expanduser().resolve()
    try:
        with config_path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError as exc:
        raise ConfigError(
            f"找不到配置文件：{config_path}。请先复制 config.example.toml。"
        ) from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件不是有效的 TOML：{exc}") from exc

    llm_data = _section(data, "llm")
    model = _required_text(llm_data, "model", "llm")
    small_model = llm_data.get("small_model")
    if not isinstance(small_model, str) or not small_model.strip():
        small_model = model

    context_limit = llm_data.get("context_limit", 64_000)
    if not isinstance(context_limit, int) or isinstance(context_limit, bool) or context_limit <= 0:
        raise ConfigError("[llm] context_limit 必须是正整数")

    handoff_data = _section(data, "handoff")
    trigger_ratio = handoff_data.get("trigger_ratio", 0.75)
    raw_tail = handoff_data.get("raw_tail", 20)
    if not isinstance(trigger_ratio, (int, float)) or isinstance(trigger_ratio, bool) or not 0 < trigger_ratio <= 1:
        raise ConfigError("[handoff] trigger_ratio 必须大于 0 且不大于 1")
    if not isinstance(raw_tail, int) or isinstance(raw_tail, bool) or raw_tail < 0:
        raise ConfigError("[handoff] raw_tail 必须是非负整数")

    gatekeeper_data = _section(data, "gatekeeper")
    pre_send = gatekeeper_data.get("pre_send", True)
    notes_limit = gatekeeper_data.get("voice_notes_in_context", 10)
    if not isinstance(pre_send, bool):
        raise ConfigError("[gatekeeper] pre_send 必须是布尔值")
    if not isinstance(notes_limit, int) or isinstance(notes_limit, bool) or notes_limit < 0:
        raise ConfigError("[gatekeeper] voice_notes_in_context 必须是非负整数")

    server_data = _section(data, "server")
    host = server_data.get("host", "127.0.0.1")
    port = server_data.get("port", 8787)
    if not isinstance(host, str) or not host:
        raise ConfigError("[server] host 必须是非空字符串")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65_535:
        raise ConfigError("[server] port 必须是 1 到 65535 的整数")

    base = config_path.parent
    character_data = _section(data, "character")
    storage_data = _section(data, "storage")
    return AppConfig(
        llm=LLMConfig(
            base_url=_required_text(llm_data, "base_url", "llm").rstrip("/"),
            api_key=_required_text(llm_data, "api_key", "llm"),
            model=model,
            small_model=small_model.strip(),
            context_limit=context_limit,
        ),
        character=CharacterConfig(
            prompt_file=_resolve_path(base, character_data.get("prompt_file"), "worldbook/character.md"),
            worldbook_dir=_resolve_path(base, character_data.get("worldbook_dir"), "worldbook"),
        ),
        handoff=HandoffConfig(trigger_ratio=float(trigger_ratio), raw_tail=raw_tail),
        gatekeeper=GatekeeperConfig(
            pre_send=pre_send,
            voice_notes_in_context=notes_limit,
        ),
        storage=StorageConfig(
            db_path=_resolve_path(base, storage_data.get("db_path"), "data/yuwen.db")
        ),
        server=ServerConfig(host=host, port=port),
        config_path=config_path,
    )
