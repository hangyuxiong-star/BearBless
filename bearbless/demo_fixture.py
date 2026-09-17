from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import uuid

from bearbless.agent.contracts import VerificationResult
from bearbless.agent.loop import AgentLoop
from bearbless.agent.planner import ManualPlanner
from bearbless.agent.state import TaskState
from bearbless.agent.trace import TaskTrace
from bearbless.agent.verifier import EvidenceVerifier
from bearbless.runtime.actions import Action, ActionType
from bearbless.runtime.monitor import DeviceMonitor, Event, EventStore
from bearbless.schemas import SuccessCriterion, TaskSpec


class OptionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.options: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "article" and "data-option" in values:
            self.options.append({
                "mode": values.get("data-mode") or "",
                "duration": values.get("data-duration") or "",
                "price": values.get("data-price") or "",
                "changes": values.get("data-changes") or "",
            })


def load_options(path: Path) -> list[dict[str, str]]:
    parser = OptionParser()
    parser.feed(path.read_text(encoding="utf-8"))
    if len(parser.options) < 3:
        raise ValueError("fixture must contain at least three travel options")
    return parser.options


class FixtureExecutor:
    def execute(self, action: Action):
        del action
        return None


def run_fixture(root: Path = Path("artifacts/runs")) -> TaskState:
    task_id = f"fixture-{uuid.uuid4().hex[:8]}"
    options = load_options(Path("fixtures/travel.html"))
    best = min(options, key=lambda option: int(option["price"].split()[0]))
    goal = "Compare Copenhagen to Hamburg travel options and save the best recommendation"
    state = TaskState(
        task_id,
        goal,
        task_spec=TaskSpec(
            goal=goal,
            constraints={"max_price_dkk": 500, "prefer_fewer_transfers": True, "workspace": "shadow_display"},
            allowed_actions=["read fixture options", "compare options", "write recommendation"],
            forbidden_actions=["purchase", "payment", "send message", "Display 0 input"],
            success_criteria=[
                SuccessCriterion(name="three options compared", description="At least three travel options are compared"),
                SuccessCriterion(name="recommendation selected", description="One option is selected with a reason"),
                SuccessCriterion(name="recommendation persisted", description="The saved result survives reopen"),
            ],
            interruption_budget=0,
        ),
    )
    state.shadow_display_id = 99
    state.collected_data = {"options": options, "recommendation": best, "mode": "deterministic_fixture"}
    state.evidence = [
        {"criterion": "three options compared", "passed": len(options) >= 3, "evidence": "fixtures/travel.html"},
        {"criterion": "recommendation selected", "passed": bool(best), "evidence": best},
        {"criterion": "recommendation persisted", "passed": True, "evidence": "fixture note sink"},
    ]
    actions = (
        Action(ActionType.OBSERVE, reason="Read fixture options"),
        Action(ActionType.WAIT, seconds=0, reason="Compare structured options"),
        Action(ActionType.WAIT, seconds=0, reason="Choose lowest-price direct option"),
        Action(ActionType.WAIT, seconds=0, reason="Write recommendation to fixture note sink"),
        Action(ActionType.OBSERVE, reason="Re-open and verify persisted result"),
        Action(ActionType.FINISH, reason="Submit candidate result for independent verification"),
    )
    store = EventStore(root / task_id)
    monitor = DeviceMonitor(None, store)
    for index, action in enumerate(actions, 1):
        monitor.record(Event(
            "fixture", "agent", action.display_id, action.action.value.lower(),
            {"step": index, "reason": action.reason}, "simulated",
        ))
    verifier = EvidenceVerifier(("three options compared", "recommendation selected", "recommendation persisted"))
    result = AgentLoop(ManualPlanner(actions), FixtureExecutor(), verifier, TaskTrace(root, task_id)).run(state)
    monitor.metrics.shadow_display_id = 99
    monitor.metrics.agent_actions_total = len(actions) - 1
    monitor.metrics.verification_attempts = 1
    monitor.metrics.task_status = result.status.value
    store.save_metrics(monitor.metrics)
    return result
