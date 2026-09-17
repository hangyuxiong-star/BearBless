import json

import pytest

from bearbless.dashboard_data import submit_task_request
from bearbless.task_queue import TaskQueueError, claim_next_request, finish_request


def test_queue_claim_and_completion_round_trip(tmp_path) -> None:
    path = submit_task_request("Compare Copenhagen and Hamburg options", tmp_path)
    claimed_path, payload = claim_next_request(tmp_path)
    assert claimed_path == path
    assert payload["status"] == "RUNNING"
    assert claim_next_request(tmp_path) is None
    finish_request(path, status="COMPLETED", task_id="live-123")
    completed = json.loads(path.read_text(encoding="utf-8"))
    assert completed["status"] == "COMPLETED"
    assert completed["task_id"] == "live-123"


def test_queue_rejects_nonterminal_finish_status(tmp_path) -> None:
    path = submit_task_request("Compare travel options", tmp_path)
    with pytest.raises(TaskQueueError):
        finish_request(path, status="RUNNING")
