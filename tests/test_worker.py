from bearbless import worker
from bearbless.agent.state import TaskState, TaskStatus
from bearbless.dashboard_data import submit_task_request


def test_process_one_request_claims_and_finishes(monkeypatch, tmp_path):
    path = submit_task_request("打开音乐应用播放歌曲", tmp_path)
    monkeypatch.setattr(
        worker,
        "run_general_device_task",
        lambda goal, task_spec=None: TaskState("agent-test", goal, status=TaskStatus.COMPLETED),
    )
    assert worker.process_one_request(tmp_path)
    import json
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["status"] == "COMPLETED"
    assert payload["task_id"] == "agent-test"
