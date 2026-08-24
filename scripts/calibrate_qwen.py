"""Manually calibrate Qwen3-VL image-token behavior and JPEG quality."""

import argparse
import base64
from io import BytesIO
from pathlib import Path

import httpx
from dotenv import dotenv_values
from openai import OpenAI
from PIL import Image


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
PROMPT = "Transcribe the smallest visible product specification text. Return only the text."


def env_value(name: str, default: str | None = None) -> str | None:
    for key, value in dotenv_values().items():
        if key.strip() == name and value:
            return value.strip()
    return default


def load_source(source: str) -> Image.Image:
    if source.startswith(("http://", "https://")):
        response = httpx.get(source, follow_redirects=True, timeout=30)
        response.raise_for_status()
        data = response.content
    else:
        data = Path(source).read_bytes()
    image = Image.open(BytesIO(data))
    image.seek(0)
    image.load()
    return image.convert("RGB")


def jpeg_data_uri(image: Image.Image, quality: int, max_edge: int) -> str:
    resized = image.copy()
    resized.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
    output = BytesIO()
    resized.save(output, "JPEG", quality=quality, optimize=True, subsampling=0)
    payload = base64.b64encode(output.getvalue()).decode()
    return f"data:image/jpeg;base64,{payload}"


def ask(client: OpenAI, model: str, image_url: str, max_pixels: int | None = None):
    extra_body = {"max_pixels": max_pixels} if max_pixels else None
    return client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        extra_body=extra_body,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="local image path or URL")
    parser.add_argument("--model", default="qwen3-vl-flash")
    args = parser.parse_args()

    api_key = env_value("QWEN_KEY")
    if not api_key:
        raise SystemExit("QWEN_KEY is missing from .env")
    client = OpenAI(api_key=api_key, base_url=env_value("QWEN_BASE_URL", DEFAULT_BASE_URL))
    image = load_source(args.source)

    print("Token calibration (default service behavior):")
    for edge in (2048, 1145):
        response = ask(client, args.model, jpeg_data_uri(image, 90, edge))
        print(f"edge={edge}: prompt_tokens={response.usage.prompt_tokens}")

    response = ask(client, args.model, jpeg_data_uri(image, 90, 2048), 2048 * 2048)
    print(f"edge=2048, max_pixels=4194304: prompt_tokens={response.usage.prompt_tokens}")

    print("\nQuality calibration:")
    for quality in (90, 80, 70, 65, 60):
        response = ask(client, args.model, jpeg_data_uri(image, quality, 2048), 2048 * 2048)
        text = response.choices[0].message.content
        print(f"quality={quality}: {text}")


if __name__ == "__main__":
    main()

