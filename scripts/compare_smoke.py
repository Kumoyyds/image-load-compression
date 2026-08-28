"""Run the image-set comparison against the repository's live sample pairs."""

import json
from dataclasses import replace
from pathlib import Path

from dotenv import dotenv_values

from image_load_compression import compare_many, load_compare_config


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def env_value(name: str, default: str | None = None) -> str | None:
    for key, value in dotenv_values(ROOT / ".env").items():
        if key.strip() == name and value:
            return value.strip()
    return default


def parse_pairs(path: Path) -> list[tuple[list[str], list[str]]]:
    pairs: list[tuple[list[str], list[str]]] = []
    current: list[list[str]] | None = None
    website_index: int | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# example "):
            if current is not None:
                if not all(current):
                    raise ValueError(f"{path}: each example needs two non-empty websites")
                pairs.append((current[0], current[1]))
            current = [[], []]
            website_index = None
        elif line.startswith("## website "):
            if current is None:
                raise ValueError(f"{path}: website header appears before example header")
            try:
                website_index = int(line.removeprefix("## website ").strip()) - 1
            except ValueError as exc:
                raise ValueError(f"{path}: invalid website header: {line}") from exc
            if website_index not in (0, 1):
                raise ValueError(f"{path}: website number must be 1 or 2")
        elif line.startswith("#"):
            raise ValueError(f"{path}: unrecognized header: {line}")
        elif current is None or website_index is None:
            raise ValueError(f"{path}: URL appears outside a website section")
        else:
            current[website_index].append(line)
    if current is not None:
        if not all(current):
            raise ValueError(f"{path}: each example needs two non-empty websites")
        pairs.append((current[0], current[1]))
    return pairs


def main() -> None:
    api_key = env_value("QWEN_KEY")
    if not api_key:
        raise SystemExit("QWEN_KEY is missing from .env")
    config = load_compare_config(api_key=api_key)
    base_url = env_value("QWEN_BASE_URL", DEFAULT_BASE_URL)
    if base_url:
        config = replace(config, base_url=base_url)

    results = compare_many(parse_pairs(ROOT / "data/input_compare.txt"), config)
    output_path = ROOT / "output/compare/results.jsonl"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(json.dumps(result.to_dict(), ensure_ascii=False) + "\n" for result in results),
        encoding="utf-8",
    )
    for index, result in enumerate(results, 1):
        print(f"\n=== example {index} ({result.status}) ===\n{result.comment}")
    print(f"\nWrote {output_path}")


if __name__ == "__main__":
    main()
