from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class TaskQueueError(RuntimeError):
    pass


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def claim_next_request(root: Path) -> tuple[Path, dict[str, Any]] | None:
    if not root.exists():
        return None
    for path in sorted(root.glob("request-*.json"), key=lambda item: item.stat().st_mtime):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "QUEUED":
            continue
        payload["status"] = "RUNNING"
        payload["claimed_at"] = datetime.now(timezone.utc).isoformat()
        _write_atomic(path, payload)
        return path, payload
    return None


def finish_request(path: Path, *, status: str, task_id: str | None = None, error: str | None = None) -> None:
    if status not in {"COMPLETED", "FAILED"}:
        raise TaskQueueError(f"invalid terminal request status: {status}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update({
        "status": status,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "task_id": task_id,
        "error": error,
    })
    _write_atomic(path, payload)
