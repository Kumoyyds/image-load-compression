import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .api import run
from .config import NormalizeConfig
from .models import Status


def main() -> None:
    parser = argparse.ArgumentParser(description="Normalize a batch of product images")
    parser.add_argument("input", type=Path, help="text file containing one URL per line")
    parser.add_argument("-o", "--output", type=Path, default=Path("out"))
    parser.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args()

    urls = [line.strip() for line in args.input.read_text().splitlines() if line.strip()]
    results = run(urls, NormalizeConfig(), run_id=args.run_id)
    output_dir = args.output / args.run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    for result in results:
        if result.image_bytes:
            digest = hashlib.sha256(result.url.encode()).hexdigest()[:16]
            extension = {"JPEG": "jpg"}.get(result.output_format or "", (result.output_format or "bin").lower())
            (output_dir / f"{digest}.{extension}").write_bytes(result.image_bytes)

    with (output_dir / "results.jsonl").open("w", encoding="utf-8") as stream:
        for result in results:
            stream.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")

    counts = Counter(result.status for result in results)
    layers = Counter(result.error_layer for result in results if result.error_layer)
    passthrough = sum(result.passthrough for result in results)
    print(f"run_id: {args.run_id}")
    print(f"items: {len(results)} | passthrough: {passthrough} | budget_unmet: {counts[Status.BUDGET_UNMET]}")
    print("statuses: " + ", ".join(f"{status.value}={counts[status]}" for status in Status))
    if layers:
        print("errors: " + ", ".join(f"{layer.value}={count}" for layer, count in sorted(layers.items())))
    print(f"output: {output_dir}")
