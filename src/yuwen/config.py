"""读取并校验余温的 TOML 配置。"""

from __future__ import annotations

from dataclasses import dataclass
import math
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
    name: str = "他"
    player_name: str = "你"


@dataclass(frozen=True, slots=True)
class WorldbookConfig:
    scan_depth: int = 4
    entry_budget: int = 3000
    corpus_sample: int = 8


@dataclass(frozen=True, slots=True)
class HandoffConfig:
    trigger_ratio: float = 0.75
    raw_tail: int = 20
    min_turns: int = 20


@dataclass(frozen=True, slots=True)
class InflowConfig:
    every_n_turns: int = 1


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = "bge-m3"
    batch_size: int = 32


@dataclass(frozen=True, slots=True)
class SurfacingConfig:
    enabled: bool = True
    query_depth: int = 2
    top_k: int = 4
    max_chars: int = 1500
    min_similarity: float = 0.45
    clear_threshold: float = 0.7


@dataclass(frozen=True, slots=True)
class DecayConfig:
    base_days: float = 7.0


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
    worldbook: WorldbookConfig = WorldbookConfig()
    inflow: InflowConfig = InflowConfig()
    embedding: EmbeddingConfig = EmbeddingConfig()
    surfacing: SurfacingConfig = SurfacingConfig()
    decay: DecayConfig = DecayConfig()


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


def _integer_option(
    section: dict[str, Any], key: str, section_name: str, default: int, minimum: int
) -> int:
    value = section.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        requirement = "正整数" if minimum == 1 else "非负整数"
        raise ConfigError(f"[{section_name}] {key} 必须是{requirement}")
    return value


def _ratio_option(section: dict[str, Any], key: str, name: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ConfigError(f"[{name}] {key} 必须是 0 到 1 的数")
    return float(value)


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
    raw_tail = _integer_option(handoff_data, "raw_tail", "handoff", 20, 0)
    min_turns = _integer_option(handoff_data, "min_turns", "handoff", 20, 0)
    if not isinstance(trigger_ratio, (int, float)) or isinstance(trigger_ratio, bool) or not 0 < trigger_ratio <= 1:
        raise ConfigError("[handoff] trigger_ratio 必须大于 0 且不大于 1")

    worldbook_data = _section(data, "worldbook")
    scan_depth = _integer_option(worldbook_data, "scan_depth", "worldbook", 4, 1)
    entry_budget = _integer_option(worldbook_data, "entry_budget", "worldbook", 3000, 0)
    corpus_sample = _integer_option(worldbook_data, "corpus_sample", "worldbook", 8, 0)

    inflow_data = _section(data, "inflow")
    every_n_turns = _integer_option(inflow_data, "every_n_turns", "inflow", 1, 1)

    embedding_data = _section(data, "embedding")
    embedding_base_url = embedding_data.get("base_url", "")
    embedding_api_key = embedding_data.get("api_key", "")
    embedding_model = embedding_data.get("model", "bge-m3")
    if not isinstance(embedding_base_url, str):
        raise ConfigError("[embedding] base_url 必须是字符串")
    if not isinstance(embedding_api_key, str):
        raise ConfigError("[embedding] api_key 必须是字符串")
    if not isinstance(embedding_model, str) or not embedding_model.strip():
        raise ConfigError("[embedding] model 必须是非空字符串")
    embedding_batch_size = _integer_option(embedding_data, "batch_size", "embedding", 32, 1)

    surfacing_data = _section(data, "surfacing")
    surfacing_enabled = surfacing_data.get("enabled", True)
    if not isinstance(surfacing_enabled, bool):
        raise ConfigError("[surfacing] enabled 必须是布尔值")
    surfacing_query_depth = _integer_option(surfacing_data, "query_depth", "surfacing", 2, 1)
    surfacing_top_k = _integer_option(surfacing_data, "top_k", "surfacing", 4, 1)
    surfacing_max_chars = _integer_option(surfacing_data, "max_chars", "surfacing", 1500, 0)
    surfacing_min_similarity = _ratio_option(
        surfacing_data, "min_similarity", "surfacing", 0.45
    )
    surfacing_clear_threshold = _ratio_option(
        surfacing_data, "clear_threshold", "surfacing", 0.7
    )

    decay_data = _section(data, "decay")
    base_days = decay_data.get("base_days", 7)
    if (
        isinstance(base_days, bool)
        or not isinstance(base_days, (int, float))
        or not math.isfinite(base_days)
        or base_days <= 0
    ):
        raise ConfigError("[decay] base_days 必须是正数")

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
    name = character_data.get("name", "他")
    player_name = character_data.get("player_name", "你")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError("[character] name 必须是非空字符串")
    if not isinstance(player_name, str) or not player_name.strip():
        raise ConfigError("[character] player_name 必须是非空字符串")
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
            prompt_file=_resolve_path(base, character_data.get("prompt_file"), "worldbook/core.md"),
            worldbook_dir=_resolve_path(base, character_data.get("worldbook_dir"), "worldbook"),
            name=name.strip(),
            player_name=player_name.strip(),
        ),
        handoff=HandoffConfig(
            trigger_ratio=float(trigger_ratio), raw_tail=raw_tail, min_turns=min_turns
        ),
        gatekeeper=GatekeeperConfig(
            pre_send=pre_send,
            voice_notes_in_context=notes_limit,
        ),
        storage=StorageConfig(
            db_path=_resolve_path(base, storage_data.get("db_path"), "data/yuwen.db")
        ),
        server=ServerConfig(host=host, port=port),
        config_path=config_path,
        worldbook=WorldbookConfig(scan_depth, entry_budget, corpus_sample),
        inflow=InflowConfig(every_n_turns),
        embedding=EmbeddingConfig(
            embedding_base_url.strip().rstrip("/"),
            embedding_api_key.strip(),
            embedding_model.strip(),
            embedding_batch_size,
        ),
        surfacing=SurfacingConfig(
            surfacing_enabled,
            surfacing_query_depth,
            surfacing_top_k,
            surfacing_max_chars,
            surfacing_min_similarity,
            surfacing_clear_threshold,
        ),
        decay=DecayConfig(float(base_days)),
    )
