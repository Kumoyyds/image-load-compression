from io import BytesIO

from PIL import Image

from image_normalize import ErrorLayer, NormalizeConfig, Status, normalize_image


def image_bytes(
    mode: str = "RGB",
    size: tuple[int, int] = (40, 30),
    *,
    format: str = "PNG",
    color: object = "red",
    exif: Image.Exif | None = None,
) -> bytes:
    output = BytesIO()
    Image.new(mode, size, color).save(output, format=format, exif=exif)
    return output.getvalue()


def decode(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


def test_small_image_passes_through_unchanged() -> None:
    data = image_bytes(format="PNG")
    result = normalize_image("https://example.test/no-extension", data, NormalizeConfig())

    assert result.status == Status.SUCCESS
    assert result.original_format == "PNG"
    assert result.output_format == "PNG"
    assert result.passthrough is True
    assert result.image_bytes == data


def test_large_dimensions_prevent_passthrough_even_when_bytes_fit_budget() -> None:
    data = image_bytes(size=(3000, 3000), format="PNG", color="white")
    config = NormalizeConfig(size_budget_bytes=len(data) + 100)

    result = normalize_image("large", data, config)

    assert result.passthrough is False
    assert result.final_dimensions == (2048, 2048)
    assert result.output_format == "JPEG"


def test_exif_orientation_is_applied_and_prevents_passthrough() -> None:
    exif = Image.Exif()
    exif[274] = 6
    data = image_bytes(size=(40, 20), format="JPEG", exif=exif)

    result = normalize_image("rotated", data, NormalizeConfig())

    assert result.passthrough is False
    assert result.original_dimensions == (40, 20)
    assert result.final_dimensions == (20, 40)
    assert decode(result.image_bytes).size == (20, 40)


def test_rgba_transparency_is_composited_onto_white() -> None:
    data = image_bytes(mode="RGBA", color=(255, 0, 0, 0))
    result = normalize_image("alpha", data, NormalizeConfig(size_budget_bytes=1))

    pixel = decode(result.image_bytes).convert("RGB").getpixel((10, 10))
    assert all(channel >= 250 for channel in pixel)


def test_palette_transparency_is_composited_onto_white() -> None:
    image = Image.new("P", (20, 20))
    image.putpalette([255, 0, 0] + [0, 0, 0] * 255)
    image.info["transparency"] = 0
    output = BytesIO()
    image.save(output, "PNG")

    result = normalize_image("palette", output.getvalue(), NormalizeConfig(size_budget_bytes=1))

    pixel = decode(result.image_bytes).convert("RGB").getpixel((10, 10))
    assert all(channel >= 250 for channel in pixel)


def test_small_image_is_never_upscaled_when_reencoding() -> None:
    data = image_bytes(size=(32, 16))
    result = normalize_image("small", data, NormalizeConfig(size_budget_bytes=1))

    assert result.final_dimensions == (32, 16)


def test_invalid_content_is_an_l3_failure() -> None:
    result = normalize_image("html", b"<html>not an image</html>", NormalizeConfig())

    assert result.status == Status.FAILED
    assert result.error_layer == ErrorLayer.L3
    assert result.image_bytes is None


def test_decodable_but_unsupported_format_is_an_l3_failure() -> None:
    data = image_bytes(format="BMP")
    result = normalize_image("bitmap", data, NormalizeConfig())

    assert result.status == Status.FAILED
    assert result.error_layer == ErrorLayer.L3


def test_webp_is_available_as_a_configured_output() -> None:
    data = image_bytes(size=(100, 100))
    result = normalize_image(
        "webp", data, NormalizeConfig(output_format="webp", size_budget_bytes=1)
    )

    assert result.output_format == "WEBP"
    assert decode(result.image_bytes).format == "WEBP"


def test_floor_artifact_is_retained_when_budget_cannot_be_met() -> None:
    image = Image.effect_noise((400, 400), 100).convert("RGB")
    output = BytesIO()
    image.save(output, "PNG")
    config = NormalizeConfig(size_budget_bytes=100, quality_start=70, quality_floor=65)

    result = normalize_image("complex", output.getvalue(), config)

    assert result.status == Status.BUDGET_UNMET
    assert result.final_quality == 65
    assert result.final_size_bytes > config.size_budget_bytes
    assert result.image_bytes
