# Test Plan

## Phase 0 automated tests

Run:

```bash
python -m pytest -q
```

Coverage includes:

- ADB device, display, resolution and IME parsers;
- detection of `input -d` support;
- typed subprocess timeout handling;
- `.env` configuration loading;
- mocked doctor output without a connected phone;
- rejection of a stale display ID after display recreation.
- strict Pydantic task/action/observation boundary validation;
- structured Step Verifier pass and retryable-failure cases;
- checkpoint save/load round-trip;
- fail-safe recovery from uncertain transient FSM phases.
- deterministic task-mode policy for read-only, mutating and sensitive tasks;
- strict rejection of unknown phone-model decisions;
- a ten-case heterogeneous mobile task matrix under `evals/`.
- QQ recipient extraction and the hard allowlist for `红枣桂花熊`;
- NetEase exact-title parsing plus ad/reward/VIP deterministic state handling;
- Wolt 10-point rating verification and Wolt→QQ two-stage orchestration;
- package routing, display recreation, command timeouts and read-only ADB retry rules.

## Frozen physical-device regression

Start one durable worker, then enqueue the frozen tasks from a second terminal:

```bash
python -m bearbless worker
python -m bearbless enqueue --cloud-vision-consent --task '打开网易云播放：若把你'
python -m bearbless enqueue --cloud-vision-consent --task '打开Wolt找一家高评分汉堡店，读取店名评分和地址，然后去QQ发给红枣桂花熊，但只保留草稿不要发送'
python -m bearbless enqueue --cloud-vision-consent --task '设置今天18:37的闹钟'
```

For every resulting run, require `task_status=COMPLETED` and all of
`agent_actions_targeting_primary_display`, `primary_display_agent_package_leaks`,
`ime_policy_violations`, `isolation_violations`, and `guard_violations` to be zero.
The QQ draft evidence must name only `红枣桂花熊`, reproduce the verified
Wolt name/rating/address, and record `sent=false`. A real-send rehearsal is a
separate sensitive test: its submitted task must contain the exact final message,
and it may never be generalized to another recipient.

## General-agent evaluation

`evals/mobile_tasks.json` intentionally mixes settings, music, weather,
restaurant, browser, clock and sensitive-message tasks. A live run should log
completion, step count, replans, no-effect actions, loop detections, grounding
errors, primary-display operations and isolation violations. Passing one route
or one app is not accepted as evidence of generality.

## Safe local smoke test

```bash
python -m bearbless doctor --no-hardware-probes
```

This inventories tools and a connected device but does not create a virtual display.

## Huawei P40 Pro hardware validation

With the phone unlocked, USB debugging authorized, and its screen visible:

```bash
python -m bearbless doctor
```

Review the newest report under `artifacts/doctor/`. Confirm that the temporary Settings shadow workspace appears only in the scrcpy-controlled secondary display and that Display 0 is not changed. A FAIL or UNSUPPORTED capability must be investigated; it must never trigger fallback input on Display 0.
