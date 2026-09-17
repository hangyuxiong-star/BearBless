from __future__ import annotations

import argparse
import json
from pathlib import Path

from bearbless.config import Config
from bearbless.runtime.capabilities import Doctor, save_report
from bearbless.runtime.shadow_test import ShadowTest
from bearbless.demo_fixture import run_fixture
from bearbless.device_task import DEFAULT_ROUTE_URL, run_general_device_task, run_live_browser_task
from bearbless.task_queue import claim_next_request, finish_request
from bearbless.schemas import TaskSpec


def _print_human(report: dict[str, object]) -> None:
    print("BearBless device doctor")
    print("=" * 24)
    checks = report["checks"]
    assert isinstance(checks, dict)
    for name, raw in checks.items():
        assert isinstance(raw, dict)
        marker = {"pass": "PASS", "fail": "FAIL", "unsupported": "UNSUPPORTED", "skipped": "SKIP"}.get(str(raw["status"]), str(raw["status"]).upper())
        suffix = raw.get("detail") if raw.get("detail") not in (None, "") else raw.get("error", "")
        print(f"{marker:11} {name}: {suffix}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bearbless")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="probe Android shadow-workspace capabilities")
    doctor.add_argument("--no-hardware-probes", action="store_true", help="skip virtual-display creation")
    doctor.add_argument("--json", action="store_true", help="print JSON instead of the readable summary")
    doctor.add_argument("--output-dir", type=Path, default=Path("artifacts/doctor"))
    subparsers.add_parser("shadow-test", help="run the Phase 1 shadow-workspace proof of concept")
    subparsers.add_parser("demo-fixture", help="run the deterministic travel regression without a phone")
    run = subparsers.add_parser("run", help="run a guarded task on the connected phone")
    run.add_argument("--task", required=True)
    run.add_argument("--url", default=DEFAULT_ROUTE_URL)
    worker = subparsers.add_parser("work-once", help="claim and execute one queued dashboard task")
    worker.add_argument("--queue-dir", type=Path, default=Path("artifacts/requests"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        report = Doctor(Config.load()).run(hardware_probes=not args.no_hardware_probes)
        path = save_report(report, args.output_dir)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_human(report)
        print(f"Report: {path}")
        failed = any(check["status"] == "fail" for check in report["checks"].values())
        return 1 if failed else 0
    if args.command == "shadow-test":
        report = ShadowTest(Config.load()).run()
        print("BearBless Phase 1 shadow test")
        print("=" * 32)
        for step in report["steps"]:
            print(f"{step['status'].upper():5} {step['number']}. {step['name']}: {step['detail']}")
        print(f"Report: {report['report_path']}")
        return 0 if report["passed"] else 1
    if args.command == "demo-fixture":
        state = run_fixture()
        print(f"Fixture task: {state.task_id}")
        print(f"Status: {state.status.value}")
        print(f"Recommendation: {state.collected_data['recommendation']}")
        print(f"Artifacts: artifacts/runs/{state.task_id}")
        return 0 if state.status.value == "COMPLETED" else 1
    if args.command == "run":
        state = run_live_browser_task(args.task, args.url)
        print(f"Task: {state.task_id}")
        print(f"Status: {state.status.value}")
        print(f"Steps: {state.step_index}; replans: {state.replans}")
        print(f"Artifacts: artifacts/runs/{state.task_id}")
        if state.failure_reason:
            print(f"Failure: {state.failure_reason}")
        return 0 if state.status.value == "COMPLETED" else 1
    if args.command == "work-once":
        claimed = claim_next_request(args.queue_dir)
        if claimed is None:
            print("No queued task.")
            return 0
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
            print(f"Request: {request['request_id']}")
            print(f"Task: {state.task_id}")
            print(f"Status: {state.status.value}")
            return 0 if state.status.value == "COMPLETED" else 1
        except Exception as exc:
            finish_request(path, status="FAILED", error=str(exc))
            print(f"Request failed safely: {exc}")
            return 1
    return 2
