from __future__ import annotations

from pathlib import Path
import fcntl
import os
import signal
import threading
import time

from bearbless.device_task import run_general_device_task
from bearbless.schemas import TaskSpec
from bearbless.task_queue import TaskQueueError, cancellation_requested, claim_next_request, finish_request, heartbeat_request, worker_identity


_worker_thread: threading.Thread | None = None
_worker_lock = threading.Lock()
_cancel_event = threading.Event()
_active_request_id: str | None = None


class WorkerAlreadyRunning(RuntimeError):
    pass


def stop_worker(queue_dir: Path) -> bool:
    """Stop exactly the worker recorded for this queue; never use a broad pkill."""
    pid_path = queue_dir / ".worker.pid"
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return False
    if pid == os.getpid():
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_path.unlink(missing_ok=True)
        return False
    return True


def cancel_current_task(request_id: str | None = None) -> bool:
    """Request cooperative cancellation of the task owned by this worker."""
    with _worker_lock:
        if _active_request_id is None:
            return False
        if request_id is not None and request_id != _active_request_id:
            return False
        _cancel_event.set()
        return True


def process_one_request(
    queue_dir: Path,
    *,
    worker_id: str | None = None,
    lease_seconds: float = 180.0,
) -> bool:
    global _active_request_id
    queue_dir.mkdir(parents=True, exist_ok=True)
    device_lock = (queue_dir / ".device.lock").open("a+")
    try:
        fcntl.flock(device_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        device_lock.close()
        return False
    owner = worker_id or worker_identity()
    claimed = claim_next_request(queue_dir, worker_id=owner, lease_seconds=lease_seconds)
    if claimed is None:
        fcntl.flock(device_lock.fileno(), fcntl.LOCK_UN)
        device_lock.close()
        return False
    path, request = claimed
    with _worker_lock:
        _active_request_id = str(request.get("request_id") or path.stem)
        _cancel_event.clear()
    heartbeat_stop = threading.Event()

    def maintain_lease() -> None:
        interval = max(1.0, min(30.0, lease_seconds / 3))
        while not heartbeat_stop.wait(interval):
            try:
                heartbeat_request(path, worker_id=owner, lease_seconds=lease_seconds)
            except Exception:
                _cancel_event.set()
                return

    heartbeat_thread = threading.Thread(
        target=maintain_lease,
        name=f"bearbless-lease-{owner}",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        contract = TaskSpec.model_validate(request["mission_contract"]) if request.get("mission_contract") else None
        cancel = lambda: _cancel_event.is_set() or cancellation_requested(path, worker_id=owner)
        state = run_general_device_task(
            str(request["goal"]),
            task_spec=contract,
            should_cancel=cancel,
        )
        finish_request(
            path,
            status="COMPLETED" if state.status.value == "COMPLETED" else "FAILED",
            task_id=state.task_id,
            error=state.failure_reason,
            worker_id=owner,
        )
    except Exception as exc:
        try:
            finish_request(path, status="FAILED", error=str(exc), worker_id=owner)
        except TaskQueueError:
            # A newer worker reclaimed the expired lease. The old worker must
            # not overwrite that worker's state or terminal result.
            pass
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
        with _worker_lock:
            _active_request_id = None
            _cancel_event.clear()
        fcntl.flock(device_lock.fileno(), fcntl.LOCK_UN)
        device_lock.close()
    return True


def run_worker(
    queue_dir: Path,
    *,
    poll_seconds: float = 1.0,
    lease_seconds: float = 180.0,
    stop_event: threading.Event | None = None,
) -> None:
    queue_dir.mkdir(parents=True, exist_ok=True)
    process_lock = (queue_dir / ".worker.lock").open("a+")
    try:
        fcntl.flock(process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        process_lock.close()
        raise WorkerAlreadyRunning("a BearBless worker already owns this queue") from exc
    pid_path = queue_dir / ".worker.pid"
    pid_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    owner = worker_identity()
    try:
        while stop_event is None or not stop_event.is_set():
            processed = process_one_request(
                queue_dir,
                worker_id=owner,
                lease_seconds=lease_seconds,
            )
            if not processed:
                if stop_event is not None:
                    stop_event.wait(poll_seconds)
                else:
                    time.sleep(poll_seconds)
    finally:
        try:
            if pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        fcntl.flock(process_lock.fileno(), fcntl.LOCK_UN)
        process_lock.close()


def ensure_background_worker(queue_dir: Path, *, poll_seconds: float = 1.0) -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return

        def work() -> None:
            run_worker(queue_dir, poll_seconds=poll_seconds)

        _worker_thread = threading.Thread(target=work, name="bearbless-task-worker", daemon=True)
        _worker_thread.start()
