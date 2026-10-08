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
        "\n[decay]\nbase_days = 12.5\n",
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
    ],
)
def test_load_config_rejects_invalid_new_options(
    tmp_path: Path, section: str, value: str, key: str
) -> None:
    config_file = tmp_path / "config.toml"
    write_config(config_file, f"\n[{section}]\n{value}\n")

    with pytest.raises(ConfigError, match=key):
        load_config(config_file)
