from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SUSPICIOUS = "suspicious"
    BUDGET_UNMET = "budget_unmet"


class ErrorLayer(StrEnum):
    L1 = "L1"
    L2A = "L2a"
    L2B = "L2b"
    L2C = "L2c"
    L3 = "L3"
    L4 = "L4"


@dataclass(slots=True)
class ImageResult:
    url: str
    status: Status
    original_format: str | None = None
    original_dimensions: tuple[int, int] | None = None
    original_size_bytes: int | None = None
    output_format: str | None = None
    final_dimensions: tuple[int, int] | None = None
    final_size_bytes: int | None = None
    passthrough: bool = False
    final_quality: int | None = None
    error_layer: ErrorLayer | None = None
    error_detail: str | None = None
    retry_count: int = 0
    image_bytes: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("image_bytes")
        return data

