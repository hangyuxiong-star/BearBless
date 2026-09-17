from pathlib import Path

from bearbless.agent.state import TaskStatus
from bearbless.demo_fixture import load_options, run_fixture


def test_fixture_contains_multiple_options() -> None:
    options = load_options(Path("fixtures/travel.html"))
    assert len(options) == 3


def test_fixture_demo_completes_with_independent_evidence(tmp_path: Path) -> None:
    state = run_fixture(tmp_path)
    assert state.status == TaskStatus.COMPLETED
    assert state.collected_data["recommendation"]["mode"] == "Bus"
    run = tmp_path / state.task_id
    assert (run / "events.jsonl").exists()
    assert (run / "metrics.json").exists()
    assert (run / "state.json").exists()
    assert (run / "result.json").exists()
