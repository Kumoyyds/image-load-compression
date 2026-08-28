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


CONFIG_ENV_VAR = "IMAGE_LOAD_COMPRESSION_CONFIG"

# YAML dotted path -> (NormalizeConfig field, factor applied to the YAML value).
# Every unit conversion lives here so config.yaml can stay human-friendly.
_FIELD_MAP: dict[str, tuple[str, int]] = {
    "output.max_long_edge": ("max_long_edge", 1),
    "output.size_budget_kb": ("size_budget_bytes", 1024),
    "output.format": ("output_format", 1),
    "output.jpeg_subsampling": ("jpeg_subsampling", 1),
    "quality.start": ("quality_start", 1),
    "quality.step": ("quality_step", 1),
    "quality.floor": ("quality_floor", 1),
    "fetch.concurrency": ("concurrency", 1),
    "fetch.connect_timeout": ("connect_timeout", 1),
    "fetch.read_timeout": ("read_timeout", 1),
    "fetch.max_download_mb": ("max_download_bytes", 1024 * 1024),
    "fetch.user_agent": ("user_agent", 1),
    "fetch.retries.l1": ("l1_retries", 1),
    "fetch.retries.l2a": ("l2a_retries", 1),
    "fetch.retries.l2b": ("l2b_retries", 1),
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


def load_config(path: str | os.PathLike[str] | None = None) -> NormalizeConfig:
    """Build a NormalizeConfig from a YAML file.

    Precedence: an explicit ``path``, then ``$IMAGE_LOAD_COMPRESSION_CONFIG``,
    then the config.yaml shipped inside the package. Absent keys fall back to
    the dataclass defaults, so a caller's file may override only what it cares
    about; unknown keys raise, so a typo never silently keeps the old value.
    """
    if path is None:
        path = os.environ.get(CONFIG_ENV_VAR) or None
    source = Path(path) if path is not None else resources.files(__package__) / "config.yaml"

    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{source}: top level must be a mapping")

    flat = _flatten(raw)
    unknown = sorted(set(flat) - set(_FIELD_MAP))
    if unknown:
        raise ValueError(f"{source}: unknown config keys: {', '.join(unknown)}")

    values: dict[str, Any] = {}
    for dotted, value in flat.items():
        field, factor = _FIELD_MAP[dotted]
        values[field] = value * factor if factor != 1 else value
    return NormalizeConfig(**values)
