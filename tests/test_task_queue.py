import json
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

import pytest

from bearbless.dashboard_data import submit_task_request
from bearbless.task_queue import (
    TaskQueueError,
    cancellation_requested,
    claim_next_request,
    finish_request,
    heartbeat_request,
    request_cancellation,
)


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


def test_queue_claim_is_atomic_across_workers(tmp_path) -> None:
    submit_task_request("打开音乐应用播放歌曲", tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(
            lambda owner: claim_next_request(tmp_path, worker_id=owner),
            ("worker-a", "worker-b"),
        ))
    assert sum(claim is not None for claim in claims) == 1


def test_stale_running_request_is_reclaimed_without_replaying_owner(tmp_path) -> None:
    path = submit_task_request("设置晚上六点闹钟", tmp_path)
    claim_next_request(tmp_path, worker_id="dead-worker", lease_seconds=1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["lease_until"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(payload), encoding="utf-8")

    claimed_path, recovered = claim_next_request(tmp_path, worker_id="new-worker")
    assert claimed_path == path
    assert recovered["worker_id"] == "new-worker"
    assert recovered["attempt"] == 2
    assert recovered["recovered_from_stale_lease"] is True


def test_heartbeat_requires_lease_owner(tmp_path) -> None:
    path = submit_task_request("查找一家汉堡店", tmp_path)
    claim_next_request(tmp_path, worker_id="worker-a")
    heartbeat_request(path, worker_id="worker-a")
    with pytest.raises(TaskQueueError, match="owned"):
        heartbeat_request(path, worker_id="worker-b")


def test_cancellation_crosses_process_boundary_via_request_file(tmp_path) -> None:
    path = submit_task_request("给联系人发送消息", tmp_path)
    claim_next_request(tmp_path, worker_id="worker-a")
    assert request_cancellation(path)
    assert cancellation_requested(path, worker_id="worker-a")
