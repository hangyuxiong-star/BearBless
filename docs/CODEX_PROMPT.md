# CODEX_PROMPT.md

This is the historical AI-assisted build prompt. Repository documentation now lives under `docs/`; keep `README.md` at the repository root.

---

You are the primary engineer for this repository.

Read `README.md` and `docs/PROJECT_SPEC.md` completely before editing anything.

Your job is to build **与熊同行** (English and code name: **BearBless / `bearbless`**), a non-interruptive Android agent runtime whose core requirement is:

> A human keeps using Display 0 while the agent performs a real multi-step task in a separate virtual display on the same physical Android phone.

The current target device is a Huawei P40 Pro (ELS-AN00, Android 12), but all device-specific assumptions must be capability-probed rather than hard-coded.

## Verified pre-flight findings — read before Phase 0

The shadow-workspace assumption has already been manually verified on the real target device, before this prompt was written. Treat the following as ground truth, not as something to re-derive from first principles:

```text
Virtual display creation:  PASS — `scrcpy --new-display=1080x2400/420 --start-app=...` works.
Display ID stability:      NOT STABLE — two consecutive runs allocated IDs 7 and 8.
                            Never cache or reuse a display ID across a stop/start cycle.
Content isolation:         PASS — shadow content only appeared in the scrcpy window;
                            Display 0 stayed on the home screen throughout.
Input Backend A            CONDITIONAL PASS — `adb shell input -d <id> tap x y` works
(adb shell input -d):      correctly ONLY when <id> is the display's current live ID.
                            A first test using a stale ID leaked a tap onto Display 0
                            and opened the on-screen keyboard there. Retesting with the
                            correct live ID succeeded with zero leakage.
Input Backend B             PASS — direct interaction with the scrcpy control window
(scrcpy control path):     correctly targets the shadow display, no leakage observed.
```

Implication for implementation: **both backends are usable on this device**, but `InputBackend` for Backend A must resolve the live display ID immediately before every dispatch call — this must be enforced inside the backend itself (fail loudly if given a stale/mismatched ID), not left as a caller convention that can be forgotten. Do not spend Phase 0/1 time re-discovering this from scratch; build the freshness check as a first-class behavior from the start, and write a unit test for it (stale ID is rejected or re-resolved, never silently used).

## Rules you must not violate

1. Do not redesign this into a normal foreground Android GUI agent.
2. Never silently fall back to Display 0.
3. Never hard-code the virtual display ID, and never reuse a display ID across a display's stop/start cycle — resolve it fresh at every dispatch (see verified findings above).
4. Do not start by integrating an LLM.
5. Do not make LangGraph a required dependency for the MVP.
6. Do not let the LLM generate arbitrary shell commands.
7. Do not claim `getevent` proves agent/human isolation.
8. Do not inspect Display 0 content for model context.
9. Do not use root/Xposed.
10. Keep the dependency set small and the code understandable.
11. Preserve all safety invariants in `PROJECT_SPEC.md`, including the violation-attribution algorithm in Section 3 — implement it as written, do not substitute a simpler heuristic.
12. When hardware capability is uncertain, implement a probe and report the result instead of guessing.

## Execution order

Work autonomously through these phases, **except for the mandatory stop after Phase 1 described below**.

### Phase 0
Scaffold the repository and implement:

```bash
python -m bearbless doctor
```

Include the IME policy probe via `adb shell dumpsys input_method` (see PROJECT_SPEC.md R6) and the Backend A live-ID freshness check as part of the capability probe, not as an afterthought.

Write unit tests using mocked command outputs.

### Phase 1
Implement:

```bash
python -m bearbless shadow-test
```

Create a virtual display, parse its runtime ID, launch a safe app, test observation and display-targeted input (both backends), and clean up.

**Stop here and report explicitly.** Print PASS/FAIL for each of the seven steps in PROJECT_SPEC.md Phase 1, and do not begin Phase 2 until the human operating this session confirms. This is the first phase that touches real hardware end-to-end, and every later phase assumes its result is trustworthy — a silent assumption here is more expensive to unwind later than a short pause now. If shadow-display creation or display-targeted input fails in a way not already covered by the verified findings above, do not attempt undocumented workarounds: report the exact failure and propose at most two alternative architectures for human decision, without silently implementing either.

### Phase 2
Refactor device interaction behind `ShadowDisplay`, `InputBackend`, `ObservationBackend`, and `DeviceMonitor`.

### Phase 3
Implement the Non-Interference Monitor using the violation-attribution algorithm in PROJECT_SPEC.md Section 3 (not a looser heuristic), disruption budget = 0, action schema, guard, structured artifacts and event timeline.

### Phase 4
Implement the explicit PLAN → OBSERVE → GUARD → EXECUTE → VERIFY agent state machine. First support deterministic/manual plans. Then add the optional model client.

### Phase 5
Implement the Streamlit dashboard and one end-to-end task. Before recording any live demo footage, rehearse against the 2–3 fixed, pre-verified pages described in PROJECT_SPEC.md Section 9 — do not let the agent perform open-ended search for the first time during the actual recording session.

### Phase 6
Add graceful conflict handling, final tests and documentation.

Do not attempt multiple shadow displays until Phases 0–6 work reliably.

## Required engineering behavior

Before choosing an implementation backend, inspect the connected device:

```bash
adb devices
adb shell getprop ro.product.manufacturer
adb shell getprop ro.product.model
adb shell getprop ro.build.version.release
adb shell getprop ro.build.version.sdk
adb shell input --help
adb shell dumpsys input_method
scrcpy --version
scrcpy --list-displays
```

For every external command:

- use a timeout;
- capture stdout/stderr;
- raise a typed exception or return a typed result;
- log the command category, but do not leak secrets.

For virtual-display creation, parse scrcpy output to obtain the actual display ID — every time, never cache it.

For input:

- both Backend A (`adb shell input -d DISPLAY_ID ...`) and Backend B (scrcpy control path) are confirmed usable on the target device — see verified findings above;
- Backend A must resolve the live display ID immediately before each dispatch and reject/re-resolve on mismatch, per the pre-flight findings;
- never use untargeted input as a fallback.

For observation:

- capability-probe the strategy;
- do not assume the main-display screenshot is the virtual display;
- if a shell screenshot cannot observe the shadow display, use the scrcpy video path or another verified display-specific method.

## Keep an implementation journal

Create and continuously maintain:

```text
TASKS.md
DECISIONS.md
TEST_PLAN.md
DEMO_SCRIPT.md
```

`DECISIONS.md` should include:

```text
decision
alternatives considered
why chosen
device-specific findings
failed attempts
```

Seed `DECISIONS.md` with the pre-flight verification already documented in `README.md` (virtual display creation, the stale-display-ID incident on Backend A, and why Backend A is used with a mandatory live-ID resolution rather than discarded outright) as its first entry — do not make Codex rediscover and re-document something already verified by hand.

This project will be defended orally, so failed approaches and trade-offs are valuable.

## After each phase

1. run relevant tests;
2. update `TASKS.md`;
3. update `DECISIONS.md`;
4. make a small, clear git commit;
5. print a concise summary:
   - what works;
   - what is still unverified on hardware;
   - exact next step.

Do not stop to ask broad architecture questions unless proceeding would be destructive. Make the safest reasonable engineering choice consistent with `PROJECT_SPEC.md`. The one exception to "do not stop" is the mandatory Phase 1 checkpoint above — that pause is required regardless of how confident the phase's own tests look.

## First action now

Do only Phase 0 first.

Inspect the repository, scaffold the project, implement the doctor command (including the IME probe and the Backend A freshness check) with mocked tests, run the tests, and report the exact commands I should run with the Huawei P40 Pro attached.

Do not implement the LLM agent yet.
