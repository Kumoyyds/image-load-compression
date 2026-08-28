from importlib import resources

import pytest

from image_load_compression import CompareConfig, load_compare_config, load_config


def write(tmp_path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_packaged_compare_config_loads() -> None:
    config = load_compare_config(api_key="secret")

    assert isinstance(config, CompareConfig)
    assert config.api_key == "secret"
    assert config.model == "qwen3-vl-flash"
    assert config.max_pixels == 1024 * 1024


def test_both_loaders_accept_a_file_with_both_sections(tmp_path) -> None:
    path = write(
        tmp_path,
        """output:
  max_long_edge: 800
compare:
  model: custom-vl
  concurrency: 2
""",
    )

    assert load_config(path).max_long_edge == 800
    compare_config = load_compare_config(path, api_key="injected")
    assert compare_config.model == "custom-vl"
    assert compare_config.concurrency == 2


def test_api_key_in_yaml_is_rejected_by_both_loaders(tmp_path) -> None:
    path = write(tmp_path, "compare:\n  api_key: do-not-store-this\n")

    with pytest.raises(ValueError, match="compare.api_key is not allowed in YAML"):
        load_config(path)
    with pytest.raises(ValueError, match="compare.api_key is not allowed in YAML"):
        load_compare_config(path, api_key="injected")


@pytest.mark.parametrize(
    "body,key",
    [
        ("output:\n  max_long_edg: 800\n", "output.max_long_edg"),
        ("compare:\n  max_image_per_set: 3\n", "compare.max_image_per_set"),
    ],
)
def test_typo_in_either_section_raises(tmp_path, body: str, key: str) -> None:
    with pytest.raises(ValueError, match=key):
        load_compare_config(write(tmp_path, body), api_key="injected")


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"api_key": ""}, "api_key must not be empty"),
        ({"max_images_per_set": 0}, "max_images_per_set must be positive"),
        ({"concurrency": 0}, "concurrency must be positive"),
        ({"max_retries": -1}, "max_retries cannot be negative"),
        ({"temperature": -0.1}, "temperature cannot be negative"),
    ],
)
def test_invalid_compare_values_raise(changes, message: str) -> None:
    values = {"api_key": "secret", **changes}
    with pytest.raises(ValueError, match=message):
        CompareConfig(**values)


def test_packaged_yaml_has_compare_section() -> None:
    packaged = resources.files("image_load_compression") / "config.yaml"
    assert "compare:" in packaged.read_text(encoding="utf-8")
