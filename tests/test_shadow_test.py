import pytest

from bearbless.config import Config
from bearbless.runtime.shadow_test import ShadowTest


def test_live_id_is_resolved_before_every_action() -> None:
    test = ShadowTest(Config())
    test.display.id = 8
    test.display._process = type("Process", (), {"poll": lambda self: None})()
    test.display.resolver.list_ids = lambda: [0, 8]  # type: ignore[method-assign]
    assert test.display.resolve_live_id() == 8
    with pytest.raises(RuntimeError, match="current shadow display"):
        test.input.tap(7, 1, 1)
