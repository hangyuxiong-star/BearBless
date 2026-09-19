from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import socket
from typing import Any
import uuid


class TaskQueueError(RuntimeError):
    pass


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def worker_identity() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _parse_time(value: object) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


@contextmanager
def _queue_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".queue.lock"
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def claim_next_request(
    root: Path,
    *,
    worker_id: str | None = None,
    lease_seconds: float = 180.0,
) -> tuple[Path, dict[str, Any]] | None:
    if not root.exists():
        return None
    owner = worker_id or worker_identity()
    now = datetime.now(timezone.utc)
    with _queue_lock(root):
        for path in sorted(root.glob("request-*.json"), key=lambda item: item.stat().st_mtime):
            payload = json.loads(path.read_text(encoding="utf-8"))
            status = payload.get("status")
            stale = status == "RUNNING" and _parse_time(payload.get("lease_until")) <= now.timestamp()
            if status != "QUEUED" and not stale:
                continue
            payload.update({
                "status": "RUNNING",
                "worker_id": owner,
                "claimed_at": now.isoformat(),
                "heartbeat_at": now.isoformat(),
                "lease_until": datetime.fromtimestamp(now.timestamp() + lease_seconds, timezone.utc).isoformat(),
                "attempt": int(payload.get("attempt", 0)) + 1,
                "recovered_from_stale_lease": bool(stale),
            })
            _write_atomic(path, payload)
            return path, payload
    return None


def heartbeat_request(path: Path, *, worker_id: str, lease_seconds: float = 180.0) -> None:
    now = datetime.now(timezone.utc)
    with _queue_lock(path.parent):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "RUNNING" or payload.get("worker_id") != worker_id:
            raise TaskQueueError("request lease is no longer owned by this worker")
        payload["heartbeat_at"] = now.isoformat()
        payload["lease_until"] = datetime.fromtimestamp(now.timestamp() + lease_seconds, timezone.utc).isoformat()
        _write_atomic(path, payload)


def request_cancellation(path: Path) -> bool:
    with _queue_lock(path.parent):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") not in {"QUEUED", "RUNNING"}:
            return False
        payload["cancel_requested_at"] = datetime.now(timezone.utc).isoformat()
        if payload.get("status") == "QUEUED":
            payload.update({
                "status": "FAILED",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": "cancelled by user before execution",
            })
        _write_atomic(path, payload)
        return True


def cancellation_requested(path: Path, *, worker_id: str) -> bool:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return (
        payload.get("worker_id") != worker_id
        or bool(payload.get("cancel_requested_at"))
    )


def finish_request(
    path: Path,
    *,
    status: str,
    task_id: str | None = None,
    error: str | None = None,
    worker_id: str | None = None,
) -> None:
    if status not in {"COMPLETED", "FAILED"}:
        raise TaskQueueError(f"invalid terminal request status: {status}")
    with _queue_lock(path.parent):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if worker_id is not None and payload.get("worker_id") != worker_id:
            raise TaskQueueError("cannot finish a request owned by another worker")
        payload.update({
            "status": status,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "error": error,
            "lease_until": None,
        })
        _write_atomic(path, payload)
