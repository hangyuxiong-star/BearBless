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
