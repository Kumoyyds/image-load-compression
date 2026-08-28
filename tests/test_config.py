from importlib import resources

import pytest
import yaml

from image_load_compression import NormalizeConfig, load_config
from image_load_compression.config import _FIELD_MAP, CONFIG_ENV_VAR, _flatten


def write(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_packaged_yaml_is_valid_and_complete() -> None:
    # The shipped file is the tuning surface, so its values are meant to drift from
    # the dataclass defaults. What must hold is that it parses into a valid config
    # and still lists every knob, so a newly added field is never silently unshipped.
    assert isinstance(load_config(), NormalizeConfig)

    packaged = resources.files("image_load_compression") / "config.yaml"
    keys = set(_flatten(yaml.safe_load(packaged.read_text(encoding="utf-8"))))

    assert keys == set(_FIELD_MAP)


def test_absent_keys_fall_back_to_defaults(tmp_path) -> None:
    config = load_config(write(tmp_path, "output:\n  max_long_edge: 800\n"))

    assert config.max_long_edge == 800
    assert config.size_budget_bytes == NormalizeConfig().size_budget_bytes
    assert config.quality_start == NormalizeConfig().quality_start


def test_size_units_are_converted(tmp_path) -> None:
    config = load_config(write(tmp_path, "output:\n  size_budget_kb: 80\nfetch:\n  max_download_mb: 5\n"))

    assert config.size_budget_bytes == 80 * 1024
    assert config.max_download_bytes == 5 * 1024 * 1024


def test_nested_retries_are_flattened(tmp_path) -> None:
    config = load_config(write(tmp_path, "fetch:\n  retries:\n    l2a: 7\n"))

    assert config.l2a_retries == 7
    assert config.l1_retries == NormalizeConfig().l1_retries


def test_empty_file_yields_defaults(tmp_path) -> None:
    assert load_config(write(tmp_path, "")) == NormalizeConfig()


def test_unknown_key_raises(tmp_path) -> None:
    path = write(tmp_path, "output:\n  max_long_edg: 800\n")

    with pytest.raises(ValueError, match="unknown config keys: output.max_long_edg"):
        load_config(path)


def test_invalid_value_is_rejected_by_the_dataclass(tmp_path) -> None:
    path = write(tmp_path, "output:\n  max_long_edge: 0\n")

    with pytest.raises(ValueError, match="max_long_edge must be positive"):
        load_config(path)


def test_env_var_selects_the_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(CONFIG_ENV_VAR, str(write(tmp_path, "output:\n  max_long_edge: 1024\n")))

    assert load_config().max_long_edge == 1024


def test_explicit_path_beats_the_env_var(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(CONFIG_ENV_VAR, str(write(tmp_path, "output:\n  max_long_edge: 1024\n")))
    other = tmp_path / "other.yaml"
    other.write_text("output:\n  max_long_edge: 512\n", encoding="utf-8")

    assert load_config(other).max_long_edge == 512
