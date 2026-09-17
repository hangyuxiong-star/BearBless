from io import BytesIO

from PIL import Image

from bearbless.agent.screen_state import fingerprint_distance, same_screen, screen_fingerprint


def png(color: tuple[int, int, int]) -> bytes:
    image = Image.new("RGB", (40, 60), color)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_fingerprint_is_deterministic():
    first = screen_fingerprint(png((10, 10, 10)))
    second = screen_fingerprint(png((10, 10, 10)))
    assert first == second
    assert fingerprint_distance(first, second) == 0
    assert same_screen(first, second)


def test_missing_fingerprint_is_not_same_screen():
    assert not same_screen(None, screen_fingerprint(png((0, 0, 0))))


def test_small_picker_region_change_is_not_erased():
    base = Image.new("RGB", (1080, 2400), "white")
    changed = base.copy()
    for x in range(510, 570):
        for y in range(340, 790):
            changed.putpixel((x, y), (20, 20, 20))
    first = BytesIO()
    second = BytesIO()
    base.save(first, format="PNG")
    changed.save(second, format="PNG")
    assert not same_screen(screen_fingerprint(first.getvalue()), screen_fingerprint(second.getvalue()))
