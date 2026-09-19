from __future__ import annotations

from io import BytesIO
import hashlib

from PIL import Image, UnidentifiedImageError


def screen_fingerprint(png: bytes, *, size: int = 64) -> str:
    """Return a perceptual fingerprint that retains small widget changes.

    A 16x16 average hash erased Android picker-wheel changes occupying roughly
    0.1% of the screen and falsely classified successful time adjustments as
    no-ops. 64x64 remains compact while preserving those local transitions.
    """
    try:
        image = Image.open(BytesIO(png)).convert("L").resize((size, size))
    except UnidentifiedImageError:
        return hashlib.sha256(png).hexdigest()
    pixels = list(image.getdata())
    mean = sum(pixels) / len(pixels)
    bits = "".join("1" if pixel >= mean else "0" for pixel in pixels)
    return f"{int(bits, 2):0{size * size // 4}x}"


def fingerprint_distance(left: str, right: str) -> int:
    if len(left) != len(right):
        return max(len(left), len(right)) * 4
    return (int(left, 16) ^ int(right, 16)).bit_count()


def same_screen(left: str | None, right: str | None, *, max_distance: int = 2) -> bool:
    return bool(left and right and fingerprint_distance(left, right) <= max_distance)
