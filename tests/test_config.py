from pathlib import Path

import pytest

from yuwen.config import AuthConfig, ConfigError, load_config, validate_auth_password


def write_config(path: Path, extra: str = "") -> None:
    path.write_text(
        """
[llm]
base_url = "https://example.test/v1/"
api_key = "test-key"
model = "test-model"

[character]
prompt_file = "character.md"
worldbook_dir = "books"

[storage]
db_path = "state/test.db"
"""
        + extra,
        encoding="utf-8",
    )


def test_load_config_applies_defaults_and_resolves_paths(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    write_config(config_file)

    config = load_config(config_file)

    assert config.llm.base_url == "https://example.test/v1"
    assert config.llm.small_model == "test-model"
    assert config.llm.context_limit == 64_000
    assert config.character.prompt_file == tmp_path / "character.md"
    assert config.character.worldbook_dir == tmp_path / "books"
    assert config.character.name == "他"
    assert config.character.player_name == "对方"
    assert config.worldbook.scan_depth == 4
    assert config.worldbook.entry_budget == 3000
    assert config.worldbook.corpus_sample == 8
    assert config.storage.db_path == tmp_path / "state/test.db"
    assert config.handoff.trigger_ratio == 0.75
    assert config.handoff.min_turns == 20
    assert config.inflow.every_n_turns == 1
    assert config.embedding.base_url == ""
    assert config.embedding.model == "bge-m3"
    assert config.embedding.batch_size == 32
    assert config.surfacing.top_k == 4
    assert config.surfacing.min_similarity == 0.45
    assert config.decay.base_days == 7
    assert config.todo.enabled
    assert config.todo.max_in_context == 3
    assert config.todo.cooldown_hours == 12
    assert config.todo.stale_days == 30
    assert config.todo.max_open_in_prompt == 10
    assert config.forget.enabled and config.forget.recent_memories == 5
    assert config.anniversary.enabled
    assert config.wake.enabled
    assert config.wake.timezone == ""
    assert config.wake.active_hours == "09:00-23:00"
    assert (config.wake.min_interval_minutes, config.wake.max_interval_minutes) == (40, 120)
    assert (config.wake.min_silence_minutes, config.wake.max_per_day, config.wake.max_unanswered) == (60, 3, 2)
    assert config.wake.notify_url == "" and config.wake.notify_format == "text"
    assert config.server.port == 8787


def test_load_config_rejects_missing_required_value(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("[llm]\nmodel = 'x'\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="base_url"):
        load_config(config_file)


@pytest.mark.parametrize("password", ["", "short", "请设置至少 8 位密码", " 请设置至少 8 位密码 "])
def test_auth_password_must_be_set_before_startup(password: str) -> None:
    with pytest.raises(ConfigError, match="config.toml 的 \\[auth\\].*password"):
        validate_auth_password(AuthConfig(password))


def test_load_config_rejects_invalid_trigger_ratio(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    write_config(config_file, "\n[handoff]\ntrigger_ratio = 1.5\n")

    with pytest.raises(ConfigError, match="trigger_ratio"):
        load_config(config_file)


def test_load_config_accepts_new_options(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    write_config(
        config_file,
        "\n[worldbook]\nscan_depth = 6\nentry_budget = 500\ncorpus_sample = 3\n"
        "\n[handoff]\nmin_turns = 9\n"
        "\n[inflow]\nevery_n_turns = 3\n"
        "\n[embedding]\nbase_url = 'https://embed.test/v1/'\n"
        "api_key = 'embed-key'\nmodel = 'new-model'\nbatch_size = 8\n"
        "\n[surfacing]\nenabled = false\nquery_depth = 3\ntop_k = 5\n"
        "max_chars = 600\nmin_similarity = 0.6\nclear_threshold = 0.8\n"
        "\n[decay]\nbase_days = 12.5\n"
        "\n[todo]\nenabled = false\nmax_in_context = 2\n"
        "cooldown_hours = 6\nstale_days = 20\nmax_open_in_prompt = 7\n"
        "\n[forget]\nenabled = false\nrecent_memories = 8\n"
        "\n[anniversary]\nenabled = false\n"
        "\n[wake]\nenabled = false\ntimezone = 'Asia/Shanghai'\n"
        "active_hours = '08:30-21:45'\nmin_interval_minutes = 15\n"
        "max_interval_minutes = 90\nmin_silence_minutes = 20\n"
        "max_per_day = 4\nmax_unanswered = 1\n"
        "notify_url = 'https://example.test/topic'\nnotify_format = 'json'\n",
    )
    config_file.write_text(
        config_file.read_text(encoding="utf-8").replace(
            'worldbook_dir = "books"',
            'worldbook_dir = "books"\nname = "林舟"\nplayer_name = "小雨"',
        ),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.character.name == "林舟"
    assert config.character.player_name == "小雨"
    assert config.worldbook.scan_depth == 6
    assert config.worldbook.entry_budget == 500
    assert config.worldbook.corpus_sample == 3
    assert config.handoff.min_turns == 9
    assert config.inflow.every_n_turns == 3
    assert config.embedding.base_url == "https://embed.test/v1"
    assert config.embedding.api_key == "embed-key"
    assert config.embedding.model == "new-model"
    assert config.embedding.batch_size == 8
    assert not config.surfacing.enabled
    assert config.surfacing.query_depth == 3
    assert config.surfacing.top_k == 5
    assert config.surfacing.max_chars == 600
    assert config.surfacing.min_similarity == 0.6
    assert config.surfacing.clear_threshold == 0.8
    assert config.decay.base_days == 12.5
    assert not config.todo.enabled
    assert config.todo.max_in_context == 2
    assert config.todo.cooldown_hours == 6
    assert config.todo.stale_days == 20
    assert config.todo.max_open_in_prompt == 7
    assert not config.forget.enabled and config.forget.recent_memories == 8
    assert not config.anniversary.enabled
    assert not config.wake.enabled
    assert config.wake.timezone == "Asia/Shanghai"
    assert config.wake.active_hours == "08:30-21:45"
    assert config.wake.min_interval_minutes == 15 and config.wake.max_interval_minutes == 90
    assert config.wake.notify_format == "json"


@pytest.mark.parametrize(
    "section,value,key",
    [
        ("worldbook", "scan_depth = 0", "scan_depth"),
        ("worldbook", "entry_budget = -1", "entry_budget"),
        ("handoff", "min_turns = -1", "min_turns"),
        ("inflow", "every_n_turns = 0", "every_n_turns"),
        ("embedding", "batch_size = 0", "batch_size"),
        ("embedding", "model = ''", "model"),
        ("surfacing", "query_depth = 0", "query_depth"),
        ("surfacing", "top_k = 0", "top_k"),
        ("surfacing", "min_similarity = 1.2", "min_similarity"),
        ("surfacing", "enabled = 'yes'", "enabled"),
        ("decay", "base_days = 0", "base_days"),
        ("decay", "base_days = nan", "base_days"),
        ("todo", "enabled = 'yes'", "enabled"),
        ("todo", "max_in_context = 0", "max_in_context"),
        ("todo", "cooldown_hours = -1", "cooldown_hours"),
        ("todo", "stale_days = 0", "stale_days"),
        ("todo", "max_open_in_prompt = 0", "max_open_in_prompt"),
        ("forget", "enabled = 'yes'", "enabled"),
        ("forget", "recent_memories = -1", "recent_memories"),
        ("anniversary", "enabled = 'yes'", "enabled"),
        ("wake", "active_hours = '9:00-23:00'", "active_hours"),
        ("wake", "active_hours = '09:60-23:00'", "active_hours"),
        ("wake", "timezone = 'No/Such_Zone'", "timezone"),
        ("wake", "min_interval_minutes = 0", "min_interval_minutes"),
        ("wake", "notify_format = 'xml'", "notify_format"),
    ],
)
def test_load_config_rejects_invalid_new_options(
    tmp_path: Path, section: str, value: str, key: str
) -> None:
    config_file = tmp_path / "config.toml"
    write_config(config_file, f"\n[{section}]\n{value}\n")

    with pytest.raises(ConfigError, match=key):
        load_config(config_file)


def test_wake_interval_order_is_checked(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    write_config(config_file, "\n[wake]\nmin_interval_minutes = 121\nmax_interval_minutes = 120\n")
    with pytest.raises(ConfigError, match="min_interval_minutes.*max_interval_minutes"):
        load_config(config_file)
