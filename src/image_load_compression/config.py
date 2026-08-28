import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class NormalizeConfig:
    max_long_edge: int = 2048
    size_budget_bytes: int = 300 * 1024
    output_format: str = "JPEG"
    jpeg_subsampling: int = 0
    quality_start: int = 90
    quality_step: int = 5
    quality_floor: int = 65
    concurrency: int = 8
    connect_timeout: float = 5.0
    read_timeout: float = 20.0
    l1_retries: int = 2
    l2a_retries: int = 3
    l2b_retries: int = 2
    max_download_bytes: int = 20 * 1024 * 1024
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
    )

    def __post_init__(self) -> None:
        output_format = self.output_format.upper()
        if output_format not in {"JPEG", "WEBP"}:
            raise ValueError("output_format must be JPEG or WEBP")
        if not 1 <= self.quality_floor <= self.quality_start <= 100:
            raise ValueError("quality must satisfy 1 <= floor <= start <= 100")
        for name in (
            "max_long_edge",
            "size_budget_bytes",
            "quality_step",
            "concurrency",
            "connect_timeout",
            "read_timeout",
            "max_download_bytes",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if min(self.l1_retries, self.l2a_retries, self.l2b_retries) < 0:
            raise ValueError("retry counts cannot be negative")
        object.__setattr__(self, "output_format", output_format)


@dataclass(frozen=True, slots=True)
class CompareConfig:
    api_key: str
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model: str = "qwen3-vl-flash"
    max_images_per_set: int = 6
    concurrency: int = 4
    request_timeout: float = 90.0
    max_retries: int = 2
    max_pixels: int = 1024 * 1024
    temperature: float = 0.0
    max_output_tokens: int = 700

    def __post_init__(self) -> None:
        for name in ("api_key", "base_url", "model"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be empty")
        for name in (
            "max_images_per_set",
            "concurrency",
            "request_timeout",
            "max_pixels",
            "max_output_tokens",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.temperature < 0:
            raise ValueError("temperature cannot be negative")


CONFIG_ENV_VAR = "IMAGE_LOAD_COMPRESSION_CONFIG"

# YAML dotted path -> (target config, dataclass field, factor applied to the YAML value).
# Every unit conversion lives here so config.yaml can stay human-friendly.
_FIELD_MAP: dict[str, tuple[str, str, int]] = {
    "output.max_long_edge": ("normalize", "max_long_edge", 1),
    "output.size_budget_kb": ("normalize", "size_budget_bytes", 1024),
    "output.format": ("normalize", "output_format", 1),
    "output.jpeg_subsampling": ("normalize", "jpeg_subsampling", 1),
    "quality.start": ("normalize", "quality_start", 1),
    "quality.step": ("normalize", "quality_step", 1),
    "quality.floor": ("normalize", "quality_floor", 1),
    "fetch.concurrency": ("normalize", "concurrency", 1),
    "fetch.connect_timeout": ("normalize", "connect_timeout", 1),
    "fetch.read_timeout": ("normalize", "read_timeout", 1),
    "fetch.max_download_mb": ("normalize", "max_download_bytes", 1024 * 1024),
    "fetch.user_agent": ("normalize", "user_agent", 1),
    "fetch.retries.l1": ("normalize", "l1_retries", 1),
    "fetch.retries.l2a": ("normalize", "l2a_retries", 1),
    "fetch.retries.l2b": ("normalize", "l2b_retries", 1),
    "compare.base_url": ("compare", "base_url", 1),
    "compare.model": ("compare", "model", 1),
    "compare.max_images_per_set": ("compare", "max_images_per_set", 1),
    "compare.concurrency": ("compare", "concurrency", 1),
    "compare.request_timeout": ("compare", "request_timeout", 1),
    "compare.max_retries": ("compare", "max_retries", 1),
    "compare.max_pixels": ("compare", "max_pixels", 1),
    "compare.temperature": ("compare", "temperature", 1),
    "compare.max_output_tokens": ("compare", "max_output_tokens", 1),
}


def _flatten(node: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in node.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def _read_flat(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    if path is None:
        path = os.environ.get(CONFIG_ENV_VAR) or None
    source = Path(path) if path is not None else resources.files(__package__) / "config.yaml"

    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{source}: top level must be a mapping")

    flat = _flatten(raw)
    if "compare.api_key" in flat:
        raise ValueError(
            f"{source}: compare.api_key is not allowed in YAML; pass api_key to "
            "load_compare_config()"
        )
    unknown = sorted(set(flat) - set(_FIELD_MAP))
    if unknown:
        raise ValueError(f"{source}: unknown config keys: {', '.join(unknown)}")
    return flat


def _values_for(flat: Mapping[str, Any], target: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for dotted, value in flat.items():
        mapped_target, field, factor = _FIELD_MAP[dotted]
        if mapped_target == target:
            values[field] = value * factor if factor != 1 else value
    return values


def load_config(path: str | os.PathLike[str] | None = None) -> NormalizeConfig:
    """Build a NormalizeConfig from YAML; unknown keys always raise."""
    return NormalizeConfig(**_values_for(_read_flat(path), "normalize"))


def load_compare_config(
    path: str | os.PathLike[str] | None = None, *, api_key: str
) -> CompareConfig:
    """Build a CompareConfig from YAML, with the API key supplied by the caller."""
    return CompareConfig(api_key=api_key, **_values_for(_read_flat(path), "compare"))
