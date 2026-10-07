from pathlib import Path

import pytest

from yuwen.config import ConfigError, load_config


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
    assert config.storage.db_path == tmp_path / "state/test.db"
    assert config.handoff.trigger_ratio == 0.75
    assert config.server.port == 8787


def test_load_config_rejects_missing_required_value(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    config_file.write_text("[llm]\nmodel = 'x'\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="base_url"):
        load_config(config_file)


def test_load_config_rejects_invalid_trigger_ratio(tmp_path: Path) -> None:
    config_file = tmp_path / "config.toml"
    write_config(config_file, "\n[handoff]\ntrigger_ratio = 1.5\n")

    with pytest.raises(ConfigError, match="trigger_ratio"):
        load_config(config_file)
