# 与熊同行 · BearBless Architecture

## Target architecture (2026-09 consolidation)

BearBless uses one phone-control agent inside a deterministic runtime. “More
roles” are not treated as intelligence. Generality comes from re-observing the
real shadow display before every decision, a phone-trained model, a closed
decision protocol, and evidence-based verification.

```text
Voice / text input
        │
        ▼
Mission Compiler ──► TaskMode + permissions + success criteria
        │              READ_ONLY_QUERY | MUTATING_TASK | SENSITIVE_TASK
        ▼
Skill Router ─────────► general_gui + optional alarm/media/message skill
        │
        ▼
Phone Model Adapter (replaceable)
        │              GUI-Plus / Qwen-VL verifier / Ollama fallback
        ▼
PhoneDecision (untrusted)
        │              CLICK_ELEMENT / TAP / SWIPE / TYPE / BACK / WAIT
        │              REPORT / TAKE_OVER / ABORT
        ▼
Policy Gate + Conflict Guard (deterministic)
        ▼
Shadow Display Executor ──► Step Check ──► re-observe / re-plan
        │
        ▼
Independent Verifier ──► result + isolation receipt
```

### Project layers

```text
dashboard/                 presentation only; no ADB imports
bearbless/agent/           policy, decisions, planning, verification, FSM
bearbless/runtime/         ADB/scrcpy, display-bound I/O, Guard, Monitor
bearbless/composition.py   sole production wiring point
evals/                     heterogeneous tasks and expected safety semantics
artifacts/                 atomic checkpoints, frames, evidence and metrics
```

### General capability and task skills

Every task receives `general_gui`: display-specific observation, grounded tap,
swipe, text bridge, back, page-change checks, loop prevention and isolation.
Thin task skills add semantic constraints or deterministic completion probes;
they never execute ADB directly and never bypass the common action contract.
An ordinary new app should work through `general_gui`. Add a task skill only
for a stable domain invariant such as Android MediaSession proving playback.

### Model selection

The local `qwen3-vl:4b` remains a development fallback. The current demo route
uses Alibaba GUI-Plus for grounded single-step phone decisions and
`qwen3-vl-plus` for contract compilation, protocol fallback and independent
verification. Model replacement must not change Policy, Guard or Executor.

### Observation selection

The canonical observation is the display-specific screenshot. OCR and
Set-of-Mark enrich it with inspectable targets. Global UIAutomator XML is
forbidden on the verified Huawei device because it describes Display 0 rather
than the shadow display and would cross the privacy boundary.

### Decision versus action

`PhoneDecision` is untrusted model output. It contains semantic outcomes such
as `REPORT`, `TAKE_OVER`, and `ABORT`, plus proposed GUI interactions. Only the
planner may translate an accepted decision into the narrower runtime `Action`.
The model never receives shell or arbitrary ADB capability.

### Read-only invariant

For `READ_ONLY_QUERY`, seeing the requested fact must yield `REPORT`. Toggling
a switch to make an answer “true” is a policy failure. Sensitive intent is
classified before model planning and cannot be made safe by persuasive model
text.

## Core thesis

BearBless is a mobile-agent harness, not primarily a phone app. The model is one probabilistic component inside a deterministic safety and evidence envelope.

```text
Intelligence   → task understanding, bounded planning, action proposal
Reliability    → Pydantic boundaries, handwritten FSM, Guard, verification
Isolation      → virtual display, live-ID targeting, zero fallback
Observability  → checkpoints, events, metrics, dashboard
```

## Trust hierarchy

Prefer evidence in this order:

1. deterministic runtime state;
2. Android package/activity state;
3. structured UI/text evidence;
4. OCR derived only from the shadow display;
5. VLM interpretation.

Neither OCR nor a model interpretation may override deterministic display identity or Guard decisions.

## Phase 0

```text
CLI
 ├── Config (.env + process environment)
 └── Doctor
      ├── CommandRunner (timeouts + typed errors)
      ├── AdbClient (single ADB boundary)
      ├── capability parsers
      ├── scrcpy virtual-display lifecycle probe
      └── readable output + artifacts/doctor/*.json
```

No generic agent, model client or arbitrary command execution exists in Phase 0. The doctor uses fixed command argument lists. Hardware-specific behavior is reported as a capability rather than assumed.

The bear SVG at `bearbless_icon_bear.svg` is the canonical App icon source. It is not rasterized or altered in Phase 0 because there is no Android application module yet.

## Phase 2

```text
ShadowDisplay ── owns scrcpy process and live display identity
      │
      ├── AdbDisplayInputBackend ── resolve ID → compare → dispatch
      ├── AdbScreencapBackend ───── resolve ID → compare → capture
      ├── ScrcpyControlBackend ──── display-bound control capability
      └── DeviceMonitor ─────────── Display 0 package/activity + IME metadata
```

No input backend contains an untargeted fallback. Display-ID mismatch is a hard failure.

## Phase 3

```text
Action → schema validation → ConflictGuard → before snapshot
                                            ↓
                                      safe dispatch
                                            ↓
                                       after snapshot
                                            ↓
                              deterministic attribution
                               ├── no change → continue
                               ├── human switch → log only
                               └── agent leak/IME violation → stop display
```

Operational evidence is written as sanitized JSONL events plus a metrics snapshot. This is evidence of observed runtime behavior, not a formal proof of non-interference.

## Phase 4 — dependency direction

```text
                    ┌─────────────────────────────┐
                    │          Agent              │
                    │                             │
Goal → Planner port → TaskState → Executor port  │
                    │                 │           │
                    │                 ▼           │
                    │           Verifier port     │
                    │                 │           │
                    │          COMPLETED/FAILED   │
                    └─────────────────────────────┘
                                      ▲
                                      │ contracts only
                    ┌─────────────────┴───────────┐
                    │ Composition root            │
                    │ ADB + scrcpy + guard        │
                    │ monitor + artifacts         │
                    └─────────────────────────────┘
```

Architectural rules:

- The Agent layer does not import ADB, scrcpy or subprocess code.
- The planner emits only the closed `Action` schema.
- The executor may produce observations but cannot declare success.
- Only the verifier can transition a task to `COMPLETED`.
- Isolation violations are terminal; recoverable errors consume a bounded replan.
- `composition.py` is the sole production wiring point for concrete adapters.
- Doctor, shadow test and runtime share one virtual-display lifecycle implementation.
- Pydantic validates untrusted/external task, action, observation and verification boundaries; mutable internal runtime state remains explicit Python data.
- Stable transitions are checkpointed. Recovery from `OBSERVING`, `GUARDING` or `EXECUTING` is treated as uncertain and forces bounded replanning rather than blind replay.
- Important actions may declare an `ExpectedOutcome`; those actions require a fresh shadow observation and Step Verifier result before progress continues.

Task artifacts use one stable topology:

```text
artifacts/runs/<task_id>/
├── events.jsonl
├── metrics.json
├── state.json
├── result.json
└── screens/
```

## Phase 5 — presentation boundary

The Streamlit dashboard reads task artifacts and may create a validated local task-request file. It never calls ADB, scrcpy, the planner or executor directly, so opening, refreshing or submitting a request cannot itself affect the phone. A separate runtime controller will claim queued requests. Deterministic fixture runs and live hardware runs write the same artifact schema; fixture runs are explicitly marked and are not presented as live-device evidence.
