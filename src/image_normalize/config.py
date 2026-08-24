from dataclasses import dataclass


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
