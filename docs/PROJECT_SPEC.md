# 与熊同行（BearBless / `bearbless`）— PROJECT_SPEC.md

This document is the implementation contract for the coding agent.

## 0. Product thesis

Build an Android mobile agent that completes a real task **on the same physical phone while the owner is actively using it**, without intentionally taking over the owner's visible screen, focus, keyboard, or clipboard.

The main contribution is the **runtime architecture**, not a large prompt chain.

The implementation must prioritize:

1. isolation;
2. observability;
3. fail-safe behavior;
4. verifiable task completion;
5. then model intelligence.

---

# 1. Non-negotiable requirements

## R1 — Real Android device
The final system must work with a real Android device over ADB.

Primary target for initial testing: **Huawei P40 Pro (ELS-AN00, Android 12)** — see `README.md` → *Verified device findings* for the manual pre-flight verification already performed on this exact device before any runtime code was written. Do not re-derive these findings from documentation; treat them as ground truth for this device and re-verify only if the target device changes.

Do not assume stock Android behavior.

## R2 — Shadow display
Create a virtual/secondary display and capture its runtime display ID.

Never hard-code display ID `1`, and never cache a display ID across a display's stop/start cycle — **verified on the target device**: a fresh `scrcpy --new-display` call was observed to allocate a different ID (7, then 8) across two consecutive runs. Any code path that reuses a previously-resolved ID after a display recreation is a bug, not an edge case.

## R3 — No silent fallback to Display 0
If an operation cannot be safely targeted to the shadow display, fail or re-plan.

Never silently execute the same action on Display 0.

## R4 — Explicit action targeting
All agent input actions must be associated with the shadow display ID, **resolved immediately before each dispatch, not read from cached state**.

The implementation must probe which targeting backend the connected device actually supports.

Possible backends:

### Backend A — Android shell input
First probe:

```bash
adb shell input --help
```

If the device supports:

```text
input ... -d DISPLAY_ID ...
```

use it for display-targeted tap/swipe/key/text — but only after confirming the ID passed is the display's *current* live ID. **Verified finding on the P40 Pro**: a first test using a stale ID (belonging to an already-closed display) leaked a tap onto Display 0 and opened the keyboard on the physical home screen. Retesting with the correct, live ID succeeded cleanly with zero leakage. Backend A is viable on this device, conditional on always resolving the live ID first — implement that resolution as a mandatory step inside the backend itself, not as a caller responsibility that can be forgotten.

### Backend B — scrcpy control path
If shell input targeting is absent or unreliable, provide a scrcpy-based control adapter.

**Verified finding on the P40 Pro**: direct interaction through the scrcpy control window correctly targets the shadow display with no observed leakage to Display 0. This backend has no live-ID freshness concern in the same way Backend A does, since scrcpy's own window is already bound to the display it controls.

Do not implement a fragile fallback to untargeted `adb shell input`.

## R5 — Observation backend must be probed
Do not assume `adb screencap` can always capture a virtual display.

The doctor command must test available observation strategies.

If required, use the scrcpy video stream/control architecture rather than pretending Display 0 screenshots represent the shadow display.

## R6 — Local IME + no clipboard autosync
The virtual-display launcher must request local IME policy when supported and disable scrcpy clipboard autosync.

“Supported” must be established by an actual capability probe. On the verified
Huawei ELS-AN00 (Android 12), both `--display-ime-policy=local` and `hide` are
rejected with `SecurityException: Attempted to set IME policy to an untrusted
virtual display`. BearBless therefore omits the policy on this target and uses
IME-free execution plus a terminal Display 0 keyboard guard. It must not infer
support merely because the scrcpy CLI exposes the option.

The application itself must maintain an in-memory "shadow clipboard" for agent text.

To check IME policy state programmatically (for the doctor command and the monitor), use:

```bash
adb shell dumpsys input_method
```

Parse this output for the currently focused input method's target display and window token; an IME policy violation is when the shadow app's IME session resolves to Display 0 instead of the shadow display ID. Do not attempt to infer IME violations indirectly from screenshots alone — this command gives a direct, parseable answer.

## R7 — Fail-safe isolation
If an agent-triggered activity leaks to Display 0, log an isolation violation and stop/pause/re-plan.

## R8 — Verifier is separate from executor
An action succeeding is not equal to a task succeeding.

The task becomes COMPLETED only after explicit evidence passes the verifier.

### Runtime failure taxonomy

Every failed step receives one stable code. Prefer deterministic evidence over another model call:

| Code | Deterministic trigger where possible | Default policy |
|---|---|---|
| `INTENT_ERROR` | No verifiable goal can be compiled | Stop and ask for clarification |
| `APP_RESOLUTION_ERROR` | Chosen package is absent or cannot launch safely | Stop; never guess |
| `GROUNDING_ERROR` | Target is absent or outside the current shadow observation | Re-observe, then gently replan |
| `ACTION_SCHEMA_ERROR` | Typed action fails bounded validation | Normalize safe fields or replan; never dispatch |
| `NO_EFFECT` | Before/after shadow fingerprints are equal after settling | Ban the repeated action and gently replan |
| `WRONG_PAGE` | Screen changed but expected evidence is absent | Replan from the new observation |
| `LOADING` | Deterministic loading signal or changing frames without stable content | Bounded wait |
| `LOOP_DETECTED` | The same fingerprint/action pair repeats | Stop repetition; replan within budget |
| `VERIFICATION_ERROR` | Final evidence fails TaskSpec criteria | Fail or gently replan |
| `ISOLATION_ERROR` | Display 0, IME or focus isolation is violated | Terminal failure; zero retries |

Use model interpretation only when deterministic comparison cannot determine whether a changed page satisfies the declared expected outcome.

### Split retry budgets

`interruption_budget = 0` remains invariant. Retries are separated by risk:

- **Gentle replan budget:** ordinary shadow taps, swipes, waits and observations; default 3.
- **Sensitive-action retry budget:** app launch and system-UI-capable actions; default 0 after first dispatch.
- **Isolation retry budget:** always 0.

An `OPEN_APP` action must never be replayed merely because later grounding failed.

### UI hierarchy privacy boundary

On the verified Huawei ELS-AN00, global `adb shell uiautomator dump` returns Display 0's hierarchy while the Agent uses a separate scrcpy virtual display. Global UIAutomator XML is therefore forbidden as Agent observation: it is incorrectly grounded and may expose the user's primary-screen content. Shadow grounding must use display-specific screenshots, OCR and visual Set-of-Mark unless a display-scoped accessibility source is independently proven.

---

# 2. Build order

Implement in this order. Do not start with VLM integration.

## Phase 0 — Repository + doctor

Deliver:

```text
python -m bearbless doctor
```

Output structured JSON plus readable terminal output.

Gather:

- adb path/version;
- scrcpy path/version;
- connected device serial;
- manufacturer;
- model;
- Android release;
- SDK;
- device resolution;
- `scrcpy --list-displays`;
- feature/capability probes;
- `adb shell input --help` display-targeting support;
- app packages likely usable for Settings/browser/notes;
- virtual-display creation result;
- discovered virtual display ID;
- observation backend result;
- input backend result, **including a live-ID freshness check for Backend A** (create a display, resolve its ID, tear it down, create a new one, and confirm the backend rejects or re-resolves rather than reusing the old ID).
- IME policy state via `adb shell dumpsys input_method`.

Save reports under:

```text
artifacts/doctor/<timestamp>.json
```

Tests must mock subprocess output so Phase 0 is testable without a phone.

### Definition of done
`doctor` never mutates the main display beyond what is necessary for an explicit probe, and clearly labels all unsupported features.

---

## Phase 1 — Shadow Workspace PoC

Deliver:

```text
python -m bearbless shadow-test
```

Behavior:

1. create virtual display via scrcpy;
2. parse its actual display ID;
3. launch Android Settings (or configured safe package) on the shadow display;
4. show/obtain shadow-display frames;
5. perform a display-targeted test tap/swipe;
6. cleanly close the display;
7. print PASS/FAIL for each step.

Keep Display 0 available for manual use.

No LLM is required.

### Definition of done

Human manually performs:

```text
open another app
type text
swipe
switch apps
```

on Display 0 while `shadow-test` runs.

The shadow display continues operating and does not intentionally take over Display 0.

### Human checkpoint — mandatory stop

Phase 1 is the first phase that exercises real hardware end-to-end; everything in Phase 2 onward is built on the assumption that Phase 1's result is trustworthy. **After Phase 1 completes on real hardware, stop and report PASS/FAIL for each of the seven steps explicitly, then wait for explicit human confirmation before starting Phase 2.** If shadow-display creation or display-targeted input fundamentally fails after reasonable troubleshooting, do not attempt undocumented workarounds — report the exact failure and propose at most two alternative architectures for human decision, without silently implementing either.

---

## Phase 2 — Runtime abstraction

Implement interfaces similar to:

```python
class ShadowDisplay:
    id: int
    start()
    stop()
    launch_app(package: str)

class InputBackend:
    tap(display_id, x, y)
    swipe(display_id, x1, y1, x2, y2, duration_ms)
    type_text(display_id, text)
    key(display_id, keycode)

class ObservationBackend:
    capture(display_id) -> Frame

class DeviceMonitor:
    snapshot() -> DeviceState
    record_agent_action(action)
    detect_isolation_violation(before, after, action)
```

Backends must be replaceable.

Do not spread raw `subprocess.run("adb ...")` calls across the agent code.

---

# 3. Device monitor semantics

Do not overclaim "formal zero interference".

The monitor provides operational evidence.

Track:

```text
agent_actions_total
agent_actions_targeting_primary_display
shadow_display_id
primary_display_agent_package_leaks
ime_policy_violations
clipboard_autosync_enabled
isolation_violations
replans
verification_attempts
task_status
```

`agent_actions_targeting_primary_display` must remain `0` by construction.

## Violation attribution algorithm

"Attribute violations conservatively" is a principle, not an algorithm — make it concrete so behavior is deterministic and testable, rather than left to whatever the implementation happens to produce:

```text
For each agent-triggered action A with target shadow_display_id D:

before = monitor.snapshot()          # includes Display 0 foreground package/activity
execute A on display D
after  = monitor.snapshot()

is_violation(before, after, A) := True only if ALL of:
  1. Display 0's foreground package changed between before/after, AND
  2. the new Display 0 foreground package equals the package that A just
     targeted or launched on the shadow display, AND
  3. this Display 0 change occurred within a short attribution window
     (e.g. the same monitor polling interval as A's execution, not any
     later point in the run)

If Display 0's foreground package changed to something NOT related to A's
target package, treat it as ordinary human app-switching — NOT a violation.

If condition 1 holds but 2 or 3 does not, do not record a violation; log it
as an "unattributed Display 0 change" for visibility, but do not count it
toward isolation_violations.
```

This keeps the demo's headline metric (`isolation_violations: 0`) meaningful rather than either hair-trigger (pausing on ordinary human app-switching) or too lax to catch a real leak.

Because the user may legitimately switch apps during the same window, do not classify every Display 0 foreground change as an agent violation — the algorithm above is how that principle is enforced in code.

Store an event timeline:

```json
{
  "ts": "...",
  "actor": "agent",
  "display_id": 3,
  "type": "tap",
  "payload": {"x": 510, "y": 870},
  "result": "ok"
}
```

---

# 4. Disruption budget

Add to runtime state:

```python
interruption_budget = 0
```

Any action known to require taking over Display 0 is forbidden.

If the planner proposes one:

```text
reject action
record guard violation
re-plan
```

A slower safe path is preferred over a faster interfering path.

---

# 5. Agent state

Use a typed state object.

Suggested fields:

```python
TaskState(
    task_id: str,
    goal: str,
    status: Literal[
        "PENDING",
        "PLANNING",
        "RUNNING",
        "VERIFYING",
        "COMPLETED",
        "FAILED"
    ],
    shadow_display_id: int | None,
    step_index: int,
    max_steps: int,
    replans: int,
    max_replans: int,
    plan: list,
    collected_data: dict,
    last_observation: ...,
    action_history: list,
    evidence: list,
    interruption_budget: int = 0,
)
```

Persist task traces under:

```text
artifacts/runs/<task_id>/
```

including:

```text
events.jsonl
state.json
screens/
result.json
```

---

# 6. Action schema

The planner/executor contract must be structured.

Example:

```json
{
  "action": "TAP",
  "display_id": 3,
  "x": 510,
  "y": 870,
  "reason": "Open the first search result"
}
```

Allowed actions:

```text
OPEN_APP
TAP
SWIPE
TYPE
KEY
BACK
WAIT
OBSERVE
FINISH
```

Do not accept arbitrary shell commands from the LLM.

Validate:

- action type;
- required fields;
- coordinate range;
- display ID equals current shadow display ID (resolved fresh, per R2/R4 — reject the action if the display_id it carries does not match the live ID at guard time, do not silently correct it);
- text length;
- step budget.

---

# 7. Agent loop

Use an explicit state machine.

```text
PLAN
 ↓
OBSERVE
 ↓
CHOOSE ACTION
 ↓
GUARD
 ├── unsafe → REPLAN
 └── safe
       ↓
     EXECUTE
       ↓
     OBSERVE
       ↓
   STEP CHECK
       ├── continue
       ├── retry
       └── final candidate
              ↓
            VERIFY
              ├── pass → COMPLETED
              └── fail → REPLAN / FAILED
```

Do not hide this logic inside a giant LLM prompt.

LangGraph is optional. The first implementation should remain understandable without it.

---

# 8. Model client

Implement a provider abstraction:

```python
class ModelClient:
    def plan(...)
    def choose_action(...)
    def verify(...)
```

Configuration through:

```text
LLM_API_KEY
LLM_BASE_URL
LLM_MODEL
```

Support OpenAI-compatible multimodal requests if available.

The rest of the system must be runnable in a deterministic/manual mode without an API key so runtime development is not blocked by model integration.

---

# 9. Demo task

Primary live-demo template:

> Play one fixed, rehearsed, account-accessible NetEase song through an exact
> app deep link while the user continues typing or switching apps on Display 0.

The user must be able to keep using Display 0 during the task.

The task should contain:

```text
resolve exact title without Android text focus
→ launch orpheus://song/<id> on the shadow display
→ observe the player
→ tap the existing play control
→ verify exact title and PLAYING through MediaSession
→ stop immediately
```

The primary path must not open an app search page, tap a text field, request an
input connection, or invoke the system IME. If exact deep-link resolution is
unavailable, it fails before creating the shadow display. The fixed DSB route
URL is the non-media backup; open-ended search is not a recording-day fallback.

Do not perform purchases, payments, irreversible submissions or message sending.

## Live-demo stability constraint

Open-ended web search introduces a failure mode independent of the shadow-display architecture: CAPTCHAs, anti-bot detection, and page-structure changes on live travel sites can strand a vision-driven agent mid-task during the exact few minutes being recorded. To keep this risk separate from (and not confused with) isolation risk:

- Identify and rehearse against 2–3 specific, stable, direct-link pages *before* the recording session, rather than letting the agent perform an open search on the day of the demo.
- If a chosen site changes behavior during rehearsal, replace it — do not attempt to make the agent more "robust" to an unstable target under time pressure.
- The deterministic fixture mode (Section 10) exists for regression testing; it is not a substitute for rehearsing the specific live pages that will be used in the recorded demo.

## Notes destination

`NOTES_PACKAGE` must be configurable.

If the phone's native notes app cannot safely run on the shadow display, implement a very small optional `GhostNotes` companion app as a deterministic local sink.

Using GhostNotes is acceptable for result persistence; the information-gathering portion should still use a real app/browser when possible.

---

# 10. Deterministic test mode

Real websites are unstable.

Provide an optional local fixture mode for automated tests/demo rehearsals.

Example:

```text
fixtures/travel.html
```

with 3–5 fake but realistic travel options.

This mode exists for regression testing only.

The final video should preferably demonstrate a live task when device/network behavior is stable — see Section 9's live-demo stability constraint for how to reduce that risk without falling back to fixtures for the actual recording.

---

# 11. Dashboard

Use Streamlit unless there is a strong reason not to.

Show:

```text
Task
Status
Current step
Shadow display ID
Latest shadow frame
Last agent action

agent actions
replans
verification attempts
isolation violations
primary-display leaks
IME violations

event timeline
```

Do not render chain-of-thought.

Show concise action reasons/status only.

The dashboard must make it visually obvious that the human and the agent are acting concurrently.

---

# 12. Proof of Completion

Verification must be task-specific.

For the notes demo, verify at least:

```text
expected note title/marker is visible
at least one expected recommendation field is visible
persisted result can be re-observed after leaving/reopening the destination
```

Store evidence:

```json
{
  "criterion": "recommendation persisted",
  "passed": true,
  "evidence": "frame_0028.png"
}
```

---

# 13. Conflict / graceful degradation

Implement at least one controllable failure path.

Examples:

```text
app refuses secondary display
activity appears on Display 0
input backend cannot target virtual display
observation backend loses the shadow display
```

Expected behavior:

```text
detect
→ do not continue blindly
→ log violation
→ cleanly pause/re-plan/fail
```

For the demo, if a deterministic safe failure scenario exists, expose:

```bash
python -m bearbless demo-conflict
```

Do not intentionally disrupt the user just to make the failure demo dramatic.

---

# 14. Tests

Minimum unit tests:

```text
capability parser
display-id parser
display-id freshness check (Backend A rejects/re-resolves a stale ID)
action validation
guard rejects Display 0
step budget
replan budget
monitor attribution rules (see Section 3 algorithm — both violation and
  non-violation cases, including ordinary human app-switching)
verifier success/failure
subprocess timeout/error handling
config loading
```

Integration tests may be marked:

```text
@pytest.mark.device
```

and skipped when no Android device is connected.

---

# 15. Logging

Use structured logging.

Do not log:

```text
API keys
passwords
arbitrary user text from Display 0
sensitive clipboard contents
```

The project must not inspect the user's Display 0 content for model context.

The MVP is isolated by design.

---

# 16. Security / privacy boundary

Out of scope for MVP:

- reading user's private messages;
- scraping Display 0 screenshots for context;
- passwords;
- banking/payment flows;
- sending messages;
- deleting user data;
- root;
- Xposed;
- silently enabling Accessibility Service;
- arbitrary shell execution generated by the LLM.

---

# 17. Code quality

Requirements:

- Python type hints;
- dataclasses/Pydantic where useful;
- small modules;
- subprocess timeouts;
- explicit exceptions;
- no giant god class;
- tests for parsers and safety guards;
- comments explain non-obvious Android behavior, not obvious Python syntax.

Prefer standard library + a small dependency set.

---

# 18. Commit strategy

Use clear incremental commits, for example:

```text
chore: scaffold bearbless project
feat: add adb and device capability probe
feat: create and parse scrcpy virtual display
feat: add display-targeted input backend
feat: add shadow observation backend
feat: add non-interference monitor
feat: add guarded agent state machine
feat: add completion verifier
feat: add live dashboard
test: cover safety guard and capability parsing
docs: finalize demo and setup instructions
```

Do not make one giant final commit.

---

# 19. MVP definition

Do not work on multi-agent parallelism until all of this passes:

```text
[ ] Huawei/connected Android recognized
[ ] virtual display starts
[ ] actual display ID parsed
[ ] safe app runs on shadow display
[ ] shadow frame is observable
[ ] tap/swipe/text can be explicitly targeted, with live-ID resolution
[ ] Display 0 is never used as fallback
[ ] human can use Display 0 concurrently
[ ] monitor records runtime evidence using the Section 3 attribution algorithm
[ ] 5+ step task succeeds
[ ] final result is independently verified
[ ] dashboard presents the story clearly
```

Only then attempt the optional second shadow display.

---

# 20. Optional bonus — parallel workers

If and only if the MVP is stable:

```text
Display 0 → Human
Display A → Search Worker A
Display B → Search Worker B
```

Requirements:

- each worker has a unique display ID;
- actions may never cross display IDs;
- workers collect independent results;
- an aggregator merges results;
- failure of Worker B must not kill Worker A.

Keep this behind an experimental flag.

---

# 21. Final delivery checklist

The final repository should contain:

```text
README.md
PROJECT_SPEC.md
.env.example
requirements.txt
tests/
artifacts/.gitkeep

DEMO_SCRIPT.md
TEST_PLAN.md
ARCHITECTURE.md
DECISIONS.md
```

Codex should generate the last four during implementation.

`DECISIONS.md` must record important choices and failed approaches, especially device/ROM limitations. This will be useful in the 30-minute technical defense — the pre-flight verification in `README.md` (Verified device findings) and the stale-display-ID incident are ready-made entries for this file; do not rewrite them from scratch, carry them over as the first documented decision.
