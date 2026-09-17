from __future__ import annotations

from pathlib import Path
import threading
import time

from bearbless.device_task import run_general_device_task
from bearbless.schemas import TaskSpec
from bearbless.task_queue import claim_next_request, finish_request


_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()


def process_one_request(queue_dir: Path) -> bool:
    claimed = claim_next_request(queue_dir)
    if claimed is None:
        return False
    path, request = claimed
    try:
        contract = TaskSpec.model_validate(request["mission_contract"]) if request.get("mission_contract") else None
        state = run_general_device_task(str(request["goal"]), task_spec=contract)
        finish_request(
            path,
            status="COMPLETED" if state.status.value == "COMPLETED" else "FAILED",
            task_id=state.task_id,
            error=state.failure_reason,
        )
    except Exception as exc:
        finish_request(path, status="FAILED", error=str(exc))
    return True


def ensure_background_worker(queue_dir: Path, *, poll_seconds: float = 1.0) -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return

        def work() -> None:
            while True:
                processed = process_one_request(queue_dir)
                if not processed:
                    time.sleep(poll_seconds)

        _worker_thread = threading.Thread(target=work, name="bearbless-task-worker", daemon=True)
        _worker_thread.start()
