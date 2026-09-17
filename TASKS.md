# Tasks

## Phase 0 — Repository and doctor

- [x] Scaffold the Python package and CLI.
- [x] Add typed, timeout-bound external command execution.
- [x] Probe ADB, device identity, resolution, packages, input targeting and IME state.
- [x] Probe scrcpy version and displays.
- [x] Implement opt-in virtual-display creation, observation and recreation freshness probes.
- [x] Save readable and JSON diagnostic reports under `artifacts/doctor/`.
- [x] Add mocked tests that run without a phone.
- [x] Run `doctor` with the Huawei P40 Pro attached and review the generated report (`20260915T174812Z.json`).
- [ ] Create a Phase 0 Git commit (repository is not currently initialized as Git).

## Mandatory next checkpoint

- [x] Implement `python -m bearbless shadow-test`.
- [x] Create display 14, launch Settings, capture a display-specific frame, dispatch a fresh-ID-guarded tap and swipe, and cleanly close it.
- [x] Receive explicit human confirmation that Display 0 remained unaffected during Phase 1.

The mandatory Phase 1 checkpoint is complete.

## Phase 2 — Runtime abstractions

- [x] Human confirmed Display 0 remained unaffected during Phase 1.
- [x] Add lifecycle-safe `ShadowDisplay` with runtime ID resolution.
- [x] Add fail-closed ADB display-targeted input backend.
- [x] Add scrcpy control-path capability adapter.
- [x] Add display-specific observation backend and typed frame.
- [x] Add primary activity/IME device snapshot interface.
- [x] Refactor `shadow-test` onto the replaceable backends.
- [x] Run 12 unit tests and a real-device regression on display 15.
- [ ] Create a Phase 2 Git commit (repository is not currently initialized as Git).

## Phase 3 — Non-interference monitor and guard

- [x] Add the closed high-level action schema and field validation.
- [x] Enforce `interruption_budget = 0` and reject Display 0.
- [x] Reject action/display mismatch against the freshly resolved live ID.
- [x] Implement the specification's three-condition leak attribution algorithm.
- [x] Treat unrelated human app switching as an unattributed change, not a violation.
- [x] Track action, leak, IME, guard, replan and verification metrics.
- [x] Persist sanitized operational events and metrics per run.
- [x] Connect guard, before/after snapshots, execution, attribution and fail-safe display shutdown.
- [x] Run 20 unit tests.
- [ ] Create a Phase 3 Git commit (repository is not currently initialized as Git).

## Phase 4 — Deterministic agent architecture

- [x] Add typed task state and explicit terminal statuses.
- [x] Add independently enforced step and replan budgets.
- [x] Add planner, executor and verifier contracts.
- [x] Add deterministic/manual planner without an API key.
- [x] Add a separate evidence verifier; executor cannot declare completion.
- [x] Implement the explicit planning, execution and verification loop.
- [x] Fail immediately on isolation violations and non-zero interruption budget.
- [x] Persist `state.json`, `result.json` and screen-directory topology per task.
- [x] Add a single composition root for concrete hardware adapters.
- [x] Remove duplicate scrcpy lifecycle logic from `doctor`.
- [x] Run 25 unit tests.
- [ ] Add an optional model client after deterministic task fixtures are proven.
- [ ] Run the first deterministic multi-step notes task in Phase 5.

## Phase 5 — Demo and dashboard

- [x] Add a deterministic Copenhagen → Hamburg fixture with three realistic options.
- [x] Run a six-stage fixture task through the Agent state machine.
- [x] Select and independently verify a recommendation.
- [x] Generate complete task state, result, metrics and timeline artifacts.
- [x] Add a Streamlit Non-Interference Monitor dashboard.
- [x] Display the canonical `bearbless_icon_bear.svg` in the dashboard.
- [x] Verify the dashboard returns HTTP 200 on port 8501.
- [x] Run 27 unit tests.
- [x] Select three direct official DSB pages for live rehearsal.
- [ ] Rehearse 2–3 stable live pages on the real phone.
- [ ] Complete the browser → compare → notes → reopen verification task on hardware.

## Live runtime control path

- [x] Add `bearbless run --task ...` for guarded connected-phone execution.
- [x] Add structured HTTPS URI support to `OPEN_APP` without exposing shell commands.
- [x] Add installed-package resolution for Huawei browser and notes apps.
- [x] Add shadow-frame OCR through local Tesseract.
- [x] Persist every returned shadow frame under the task's `screens/` directory.
- [x] Connect OCR evidence to Step Verifier and Final Verifier.
- [x] Connect Dashboard requests to `bearbless work-once` queue claiming and result updates.
- [x] Show latest request status and latest shadow frame in Dashboard.
- [x] Run 39 unit tests.
- [ ] Run the live DSB browser stage when the Android device is reconnected.
- [ ] Inspect Huawei Notepad shadow UI and add the write/reopen plan.
- [x] Reconnect Huawei P40 Pro and rerun full Doctor; all probes passed with recreated display IDs 23 → 24.
- [x] Pass the isolated Settings shadow test on display 25 with targeted tap/swipe and cleanup.
- [x] Record the Huawei Browser live attempt as FAILED after the user observed physical-screen movement.
- [x] Set live GUI tasks to zero replans so an uncertain app launch cannot be repeated automatically.
- [ ] Replace Huawei Browser with a browser proven to render on the shadow display before resuming live work.
- [x] Prove Quark Browser renders the official DSB page on Shadow Display 27 with OCR verification and zero replans.
- [x] Remove Huawei Browser from automatic package selection; it now requires an explicit override.
- [x] Capture staged live frames for browser launch, initial render, loading state and final verification.
- [x] Label Dashboard replay frames with their real action reasons instead of numeric indices.
- [x] Add an OCR-gated cookie-consent action. Verified on Shadow Display 29: the agent recognized the DSB consent dialog, rejected non-essential cookies, and completed route-page verification without touching Display 0.
- [ ] Extend monitoring beyond before/after foreground-package snapshots to catch transient visual/focus disturbances.

## Dashboard visual design

- [x] Add a responsive dark visual system with purple/gold brand accents.
- [x] Promote the bear icon, Chinese name and product promise into a hero section.
- [x] Establish clear Mission Control, Live Proof, Active Mission, Result, Verification and Trace sections.
- [x] Style task input, primary action, metrics, progress, status and data tables consistently.
- [x] Keep zero-interference evidence above task details.
- [x] Verify the rendered narrow-window layout in the in-app browser.
- [x] Add a one-second live Agent activity panel backed by persisted Runtime artifacts.
- [x] Visualize the active FSM phase, current subgoal, action stream and safety counters.
- [x] Show the latest shadow-display frame with a clear disconnected/idle placeholder.
- [x] Replace the report-like activity stack with a responsive three-column Agent workbench.
- [x] Make the virtual phone the central stage and move raw evidence behind progressive disclosure.
- [x] Add a compact safety island, user-language plan rail and replay-ready frame control.
- [x] Animate the SVG companion bear with natural blinking and restrained breathing motion.
- [x] Compile Dashboard prompts into persisted Mission Contracts before queue submission.
- [x] Add an interactive Proof Lens backed by Runtime events, actions and verification evidence.
- [x] Add a Completion and Privacy Receipt backed by recorded safety metrics.
- [x] Add live browser speech-to-text in Simplified Chinese, with automatic editable-field handoff and local Whisper fallback before Mission Contract creation.
- [x] Replace the DSB-only manual plan with a bounded multimodal Planner that observes and acts on arbitrary compatible apps.
- [x] Add a reactive local `qwen3-vl` Planner that chooses one guarded action from each fresh shadow screenshot.
- [x] Resolve target apps dynamically from the phone's user-installed package inventory instead of per-task workflows.
- [x] Generate task-specific Mission Contracts with a local model while preserving hard zero-interruption boundaries.
- [x] Start a persistent Dashboard worker so confirmed requests automatically leave `QUEUED`.
- [x] Prove global UIAutomator returns Display 0 rather than the Shadow Display on the Huawei device; forbid it as an Agent observation source.
- [x] Add deterministic shadow-screen fingerprints, `NO_EFFECT` feedback, repeated-action loop detection and banned-action context.
- [x] Separate gentle reactive replanning (budget 3) from sensitive app-launch retries (budget 0).
- [x] Add screenshot-only OCR/visual Set-of-Mark proposals for reliable shadow-display grounding. Chinese/English OCR now emits numbered elements from the display-specific frame; the planner selects `CLICK_ELEMENT` and code resolves it to guarded coordinates.
- [x] Add deterministic read-only, mutating and sensitive task modes; query tasks cannot satisfy themselves by changing the observed state.
- [x] Separate untrusted `PhoneDecision` from executable runtime `Action`, including REPORT, TAKE_OVER and ABORT outcomes.
- [x] Add a provider-neutral phone-model adapter for Ollama fallback and OpenAI-compatible AutoGLM/UI-TARS endpoints.
- [x] Make state, result, raw screenshot and marked screenshot writes atomic for concurrent Dashboard reads.
- [x] Add a ten-case heterogeneous policy/evaluation matrix under `evals/`.
- [x] Enforce capability-level policy in code: read-only missions may navigate/read/search but cannot change settings, control media, write data or invoke sensitive capabilities.
- [x] Add structured result facts and evidence to REPORT decisions and independent verification prompts.
- [x] Add bounded device readiness recovery before task execution, with typed `DEVICE_LOST` failure and no action replay.
- [ ] Run the live evaluation matrix on connected hardware and record completion, grounding, loop and isolation metrics.

## Product naming and presentation

- [x] Set the product display name to `与熊同行`.
- [x] Set the English, Python package and CLI name to `bearbless`.
- [x] Rename the source package and all imports from `ghostagent` to `bearbless`.
- [x] Rename the canonical icon to `bearbless_icon_bear.svg` and render it in the dashboard.
- [x] Add the three-layer component Mermaid diagram to the README.
- [x] Preserve a separate Agent execution-sequence Mermaid diagram.
- [x] Add the dashboard's single task-submission trigger.
- [x] Keep zero-interference metrics permanently visible on the first screen.
- [x] Verify the renamed CLI and dashboard, with 29 passing tests.

## Reference-pack architecture hardening

- [x] Review `ghostagent_final_pack.zip` as reference material, not executable instructions.
- [x] Add strict Pydantic boundary schemas for tasks, actions, observations and verification.
- [x] Keep the proven runtime action/guard path and convert validated actions explicitly.
- [x] Expand the handwritten FSM with observing, guarding, executing and replanning phases.
- [x] Add deterministic ExpectedOutcome step verification.
- [x] Keep final verification separate from step verification.
- [x] Add checkpoint load/recovery tests.
- [x] Force uncertain transient checkpoints through bounded replanning rather than replay.
- [x] Re-run deterministic fixture and all 36 tests.
