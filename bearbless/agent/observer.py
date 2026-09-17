from __future__ import annotations

from bearbless.schemas import Observation
from bearbless.runtime.actions import Action
from bearbless.runtime.observation_backend import Frame


class FrameObservationBuilder:
    """Build the deterministic portion of an observation from a shadow frame.

    OCR/structured UI extraction can enrich this later; it must never source
    text from Display 0.
    """

    def build(self, frame: Frame, action: Action) -> Observation:
        return Observation(
            display_id=frame.display_id,
            captured_at=frame.captured_at,
            package=action.package if action.action.value == "OPEN_APP" else None,
            visible_text=[],
        )
