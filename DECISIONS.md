# Decisions

## 001 — Treat the verified Huawei pre-flight findings as ground truth

**Decision:** Use scrcpy's secondary display, with shell input considered usable only when the live display ID is resolved again immediately before dispatch. A display ID is never retained across display teardown/recreation.

**Alternatives considered:** Discard shell input entirely; assume a stable display ID; fall back to untargeted input.

**Why chosen:** Manual testing on the Huawei P40 Pro (ELS-AN00, Android 12, scrcpy 4.1) established that virtual display creation and scrcpy control are isolated. Display IDs changed from 7 to 8 across recreation. A stale ID caused a tap to leak to Display 0, while the current ID targeted the shadow display correctly. Untargeted fallback would violate the product's central safety invariant.

**Device-specific findings:** Secondary-display behavior is ROM-dependent. These findings apply to the verified Huawei target and must be probed again on another device.

**Failed attempt:** A stale display ID was used during manual pre-flight testing and opened the keyboard on Display 0. Phase 0 therefore includes a recreation/freshness probe and an explicit stale-ID test.

## 002 — Keep Phase 0 standard-library only

**Decision:** Implement configuration, command execution, parsers and the doctor report with the Python standard library. Pytest is a development-only dependency.

**Why chosen:** This keeps installation and hardware diagnosis small and understandable before any model or dashboard dependencies are introduced.

## 003 — Make virtual-display probes explicit and observable

**Decision:** `doctor` performs hardware probes by default; `--no-hardware-probes` provides a non-mutating inventory mode. Virtual displays are always cleaned up in `finally` paths.

**Why chosen:** The full doctor must validate the actual device, while development and CI need a safe mode that cannot launch an app or create a display.

## 004 — Phase 0 hardware result on the target phone

**Result:** PASS on Huawei ELS-AN00, Android 12 / SDK 31, with scrcpy 4.1. The doctor created display 10, destroyed it, then created display 11. The live display list was `[0, 11]`, proving display 10 was stale. Display-specific `adb screencap -d 10` worked, and `input -d DISPLAY_ID` support was detected from Huawei's bare `input` usage output.

**Failed attempt and correction:** The first implementation used scrcpy `--no-window`, which implies disabled video playback in scrcpy 4.1 and is incompatible with `--new-display`. The probe now keeps a titled diagnostic window open while the virtual display is live. Huawei also returns `Unknown command: --help` for `input --help`; capability detection now falls back to invoking `input` without a subcommand and has a regression test for this ROM behavior.

## 005 — Phase 1 shadow-workspace result

**Result:** All seven automated steps passed on runtime display 14. A 1080×2400 PNG captured directly from display 14 showed Huawei Settings. Before both the tap and swipe, the backend re-listed live displays and required the result to equal display 14; mismatches fail closed. The display was absent after scrcpy cleanup.

**Remaining human evidence:** The operator must explicitly confirm that Display 0 remained usable and unchanged during the Phase 1 actions before Phase 2 begins.

The operator confirmed that Display 0 did not move during the test.

## 006 — Phase 2 runtime boundaries

**Decision:** Centralize display lifecycle in `ShadowDisplay`, Android shell invocation in `AdbClient`, display-targeted input in `AdbDisplayInputBackend`, screenshots in `AdbScreencapBackend`, and operational state reads in `DeviceMonitor`.

**Why chosen:** Each interactive backend calls `ShadowDisplay.resolve_live_id()` immediately before dispatch. An action carrying an old or different ID fails before ADB is invoked. This makes the critical safety invariant part of the backend rather than a caller convention.

**Verification:** Twelve unit tests passed. The refactored `shadow-test` also passed all seven hardware steps on display 15 and cleaned it up successfully.

## 007 — Deterministic operational attribution, not a security-proof claim

**Decision:** Count a primary-display leak only when Display 0's package changes, the new package equals the package targeted by the agent action, and the change is within the action's attribution window. Unrelated or late changes are retained as unattributed timeline events.

**Why chosen:** This exactly implements the specification and distinguishes ordinary concurrent human app switching from an agent-caused leak without hiding suspicious state changes.

**Fail-safe behavior:** The guarded executor validates the action and live display before snapshot/dispatch, records sanitized events, compares before/after state, and stops the shadow display immediately on an attributed leak or IME violation. Text content is represented only by character count in logs.

**Verification:** Twenty unit tests pass, including violation, ordinary human switch, late matching change, Display 0 rejection, stale ID rejection and persisted metric/timeline cases.

## 008 — Ports-and-adapters Agent architecture

**Decision:** Keep the Agent dependent on small `Planner`, `ActionExecutor` and `Verifier` contracts. Wire ADB, scrcpy, guard, monitor and storage adapters only in `composition.py`.

**Alternatives considered:** Let the planner call ADB directly; build a single runtime god class; introduce LangGraph before deterministic execution works.

**Why chosen:** The state machine can now be tested without a phone or API key, hardware adapters remain replaceable, and the executor cannot mark a task complete. Isolation violations bypass replanning and fail immediately; ordinary execution/guard errors may replan only within a separate budget.

**Project cleanup:** The doctor previously retained its own early scrcpy lifecycle implementation. It now uses the same `ShadowDisplay` as runtime execution, leaving one implementation of display creation, ID discovery and cleanup.

**Verification:** Twenty-five tests pass, including completion gating, failed verification/replan exhaustion, step exhaustion, zero-interruption enforcement and task artifact persistence.

## 009 — Separate deterministic demo regression from live-page rehearsal

**Decision:** Use `fixtures/travel.html` to continuously test the complete Agent/artifact/dashboard story, while keeping real browser pages as a separate hardware rehearsal gate.

**Why chosen:** A fixture makes planner/executor/verifier and presentation regressions reproducible. It does not replace the live demo and is labeled `deterministic_fixture` in task data, so it cannot be mistaken for live evidence.

**Result:** The fixture compared three Copenhagen–Hamburg options, selected the direct 219 DKK bus, passed three independent evidence criteria and rendered through the dashboard. Twenty-seven tests pass and the dashboard returned HTTP 200.

## 010 — Product and code identity

**Decision:** The public Chinese name is **与熊同行**. The English brand, Python distribution, import package, module command and installed CLI are all `bearbless`. The canonical icon is `bearbless_icon_bear.svg`.

**Why chosen:** One code-level identifier eliminates mixed package/command terminology. The Chinese display name leads user-facing surfaces while `bearbless` remains stable for code and shell usage.

**Presentation:** README now contains both views of the architecture: a three-layer component Mermaid diagram for first impressions and a separate PLAN → OBSERVE → GUARD → EXECUTE → VERIFY diagram for execution semantics. Dashboard task submission is the sole manual trigger; submission only creates a validated local queue request and never directly executes shell commands. Zero-interference metrics remain visible above task details.

**Verification:** The old source directory and old identifier are absent outside historical artifacts. The `bearbless` CLI loads, the renamed fixture completes, twenty-nine tests pass, and the rendered dashboard visibly shows the Chinese brand, bear icon, task input, verification count and zero Display 0 actions.

## 011 — Adopt reference-pack contracts without regressing verified safety

**Decision:** Adopt the reference pack's strongest architecture ideas—Pydantic boundary contracts, explicit transient FSM phases, Step/Final Verifier separation, trust hierarchy and checkpoint recovery—while retaining this repository's already verified display-ID freshness enforcement, precise leak attribution and real Huawei results.

**Why chosen:** The ZIP is a design reference, not an authoritative replacement. Its broader harness framing improves the oral-defense narrative, but its old `ghostagent` naming and less-specific device logic must not overwrite the current “与熊同行 · BearBless” identity or tested safety behavior.

**Boundary rule:** Pydantic models validate data entering the runtime. The handwritten FSM and device adapters remain ordinary explicit Python so safety transitions stay easy to inspect.

**Recovery rule:** A checkpoint captured during observation, guarding or execution is uncertain. Restart consumes a replan and re-observes instead of blindly replaying an action that may already have happened.

**Verification:** Thirty-six tests pass, including Pydantic rejection, action conversion, Step Verifier pass/fail, checkpoint loading and transient-state recovery. The deterministic travel fixture still completes.

## 012 — Dashboard queue and first live-task stage

**Decision:** Dashboard submission writes a validated local queue request. A separate `bearbless work-once` process atomically claims one request, runs the guarded hardware task and writes its terminal status back. Dashboard never invokes ADB directly.

**Live plan stage 1:** Resolve an installed browser package, create a shadow display, open a fixed official DSB HTTPS URI through a structured `OPEN_APP`, wait, capture the shadow display, OCR it locally, verify route-page evidence and persist the frame. There is no arbitrary shell or open search surface.

**Next hardware gate:** The phone was not connected when this stage was implemented, so the code is unit-tested but the live browser and Huawei Notepad sequence remain hardware-unverified. Thirty-nine tests pass.

## 013 — Dashboard visual hierarchy

**Decision:** Use a restrained dark control-room aesthetic with violet identity, warm-gold highlights and green verified-state signals. The information order is fixed as task trigger → zero-interference proof → active mission/shadow workspace → result → verification → trace.

**Why chosen:** The dashboard must communicate the product story in one glance during a live demo. Decorative styling remains separate from runtime logic, and all safety metrics retain text labels rather than relying on color alone.

## 014 — Artifact-backed live Agent visualization

**Decision:** Refresh the Dashboard activity panel every second from the Runtime's persisted `state.json`, `metrics.json`, `events.jsonl` and shadow-display frames. Show the current subgoal and step, highlight the active FSM phase, render recent actions with reasons/results, and keep Display 0 and isolation counters alongside them.

**Why chosen:** The interface can expose what the Agent is doing without coupling Streamlit to ADB or letting presentation code control execution. The same durable evidence used for verification drives the visualization, so the demo remains auditable after a task completes.

**Offline behavior:** Before a phone frame exists, the panel explicitly says it is waiting for the shadow screen. This distinguishes a disconnected device from a rendering failure.

**Verification:** The Dashboard was visually inspected in the in-app browser and all thirty-nine tests pass.

## 015 — Virtual-phone-first interaction model

**Decision:** Present live work as a three-column control surface: a user-language plan rail, a central virtual phone, and a current-action inspector. Collapse Display 0 proof into a persistent safety island and place full evidence and Runtime traces behind progressive disclosure.

**Interaction rule:** The default view answers three questions only: what is the Agent trying to do, what is happening on the virtual phone, and is the user's primary display still untouched. Multiple captured frames gain a replay slider while the newest frame remains the default.

**Why chosen:** This preserves auditability without making raw implementation detail compete with the live device. On narrow windows the columns intentionally stack into plan → phone → action order.

**Verification:** The responsive rendered page was inspected in the in-app browser and the full thirty-nine-test suite passes.

## 016 — Motion as companion feedback

**Decision:** Animate the canonical SVG itself with two short natural blinks per cycle and a restrained breathing movement. Honor `prefers-reduced-motion` by disabling both animations.

**Why chosen:** Native SVG motion remains sharp at every size and avoids an additional GIF/video asset. The slow cadence gives the bear presence without competing with live task status.

## 017 — Contract, proof and receipt form one trust chain

**Decision:** A Dashboard request is no longer queued directly from free text. BearBless first compiles a strict `TaskSpec` Mission Contract containing allowed actions, forbidden actions, success criteria and a zero interruption budget. Only explicit confirmation writes it to the queue, and the worker validates the persisted contract before execution.

**Proof model:** Proof Lens selects a real Runtime event and joins it with action intent, expected outcome, evidence and available before/after frames. Completion Receipt renders only recorded metrics, including primary-display actions, isolation/IME violations, clipboard autosync state, verification count, replans and shadow display.

**Why chosen:** These three surfaces cover the complete trust lifecycle: scope before execution, explainability during execution, and an auditable record after execution.

**Verification:** Mission Contract interaction and all three rendered surfaces were inspected in the in-app browser. Forty tests pass, including contract persistence and bounded defaults.

## 018 — User-observed interference overrides telemetry

**Finding:** On the Huawei P40 Pro, Settings passed the isolated shadow test, but launching `com.huawei.browser` with a URL produced black shadow frames while the user observed movement on the physical screen. Before/after foreground-package snapshots reported no change because Display 0 had returned to Launcher by the time each snapshot completed.

**Decision:** Treat the run as an isolation violation regardless of the earlier telemetry, preserve the human report in the event trace, and prohibit automatic replanning for live GUI tasks. Huawei Browser is not an approved shadow-display app until a placement probe can prove otherwise.

**Why chosen:** Human-observed interference is ground truth for the product requirement. A monitor that misses a transient disturbance must be improved; its zero counter must never be used to dismiss what the user saw.

## 019 — Browser compatibility is part of the safety boundary

**Finding:** A single-attempt run with `com.quark.browser` rendered the official DSB Hamburg page on Shadow Display 27, produced a non-black screenshot, passed OCR and final verification, performed three Agent actions with zero replans, and cleaned up the display. Runtime telemetry recorded no violation; explicit user confirmation remains the stronger check for transient physical-screen movement.

**Decision:** Prefer Quark Browser, then Chrome, during automatic resolution. Huawei Browser is excluded from automatic selection on this device and may only be requested through an explicit configuration override.

**Why chosen:** `am start --display` support is application-specific in practice. An installed browser is not automatically a safe shadow-display browser, so package compatibility must be treated as a runtime capability rather than an installation check.

## 020 — Replay only real observations

**Decision:** Capture explicit observations at initial browser appearance, page-loading progress and final verification. Dashboard replay labels each persisted frame with the action reason that produced it.

**Finding:** The first staged run produced four genuine frames and exposed a DSB cookie-consent dialog. OCR could not prove the required route text, so the zero-replan policy stopped and cleaned up the task instead of hiding the failure or manufacturing a successful animation.

**Why chosen:** A trustworthy demo should reveal loading, blockers and failures as they actually occurred. Replay is evidence, not decoration.

## 021 — Gate conditional UI actions on fresh observations

**Decision:** Cookie handling uses `CONDITIONAL_TAP`: immediately capture the agent display, OCR it, and tap only when every configured consent marker is present. The action is still routed through Conflict Guard and must target the current non-primary display.

**Finding:** Live run `live-02b461200a` recognized `cookies` and `persondata` on Shadow Display 29, tapped DSB's **Afvis** control, captured the dismissed dialog, and then verified the Hamburg route page. The replay contains five genuine observations; telemetry recorded zero Display 0 actions, zero isolation violations, and zero IME violations.

**Caveat:** The consent classification is observation-gated, but the button location is currently site-specific. A future semantic locator can replace the coordinate while preserving the same guard contract.

## 022 — Voice is an input method, not an execution shortcut

**Decision:** Use the browser only to capture microphone audio, then send the growing local recording about every two seconds to a loopback-only Whisper service on `127.0.0.1`. Show each returned Simplified-Chinese transcript while the user is speaking. On stop, normalize the final transcript through OpenCC, place it into the same editable task field as typed input, and require the same Mission Contract review. Retain the one-shot local recording control as a fallback. Neither path operates the phone directly.

**Why chosen:** Voice and text must converge on one auditable Agent boundary. Keeping transcription local avoids sending microphone audio to a third-party service, while explicit text review prevents recognition errors from silently becoming device actions.

**Boundary:** This completes the voice-to-task stage. It does not make the current DSB-only `ManualPlanner` general; a bounded multimodal Planner remains the next architecture milestone.

## 023 — Generality comes from dynamic intent, capability discovery and reactive vision

**Decision:** Dashboard requests are analyzed by the local `qwen3-vl:4b` model into task-specific permissions and visual completion criteria. Before execution, the same local model selects one exact package from the phone's enumerated user-installed applications. During execution it receives the latest Shadow Display frame and returns exactly one typed action, which still passes through Conflict Guard.

**Execution rule:** The Agent replans from a fresh screenshot after every action. It may tap, swipe, type, go back or wait within bounded parameters; it cannot emit shell commands, select an uninstalled package, target Display 0, purchase, pay, send or delete.

**Queue rule:** A single Dashboard-owned background worker claims confirmed requests automatically. Queue submission is no longer presented as execution while no worker exists.

**Verification:** Dynamic package selection resolved “打开网易云，播放歌曲：这就是爱” to the installed `com.netease.cloudmusic` package from 72 third-party candidates. Fifty-one automated tests pass. The first general Meituan rehearsal safely rejected an out-of-range model wait, recorded zero Display 0 actions and zero isolation violations, and motivated parameter clamping plus terminal-state persistence.

## 024 — Global UIAutomator is forbidden for shadow grounding on this device

**Experiment:** With Settings rendered on Shadow Display 33 at 1080×2400, `adb shell uiautomator dump` exported `com.sankuai.meituan` at 1200×2486 with Display 0 controls including “搜索”, “美食” and “酒店/民宿”. The simultaneous shadow screenshot OCR contained Settings/WLAN content. Evidence is preserved under `artifacts/probes/ui-tree/20260916T192230Z/`.

**Repeat:** A second independent probe on Shadow Display 34 again showed Settings/WLAN in the display-specific screenshot, while UIAutomator exported `com.huawei.android.launcher` at 1200×2486 with Display 0 icons including “手机管家”, “备忘录”, “时钟” and “日历”. Evidence is preserved under `artifacts/probes/ui-tree/20260916T192635Z/`. The changed primary package across probes confirms UIAutomator follows Display 0 rather than the virtual display.

**Decision:** Never feed global UIAutomator XML to the Agent on this device. It is misaligned with the shadow screenshot and would expose user-screen content to planning. Grounding will use display-specific screenshots plus OCR/visual Set-of-Mark until a display-scoped accessibility source is proven.

## 025 — Ground the Agent with display-specific visual marks

**Decision:** Before every planning step, OCR the current shadow-display screenshot with local Simplified-Chinese and English models, group words into lines, draw numbered bounds, and send both the annotated frame and element catalog to the planner. The preferred model action is `CLICK_ELEMENT`; trusted runtime code converts the selected element id to its center coordinate before Conflict Guard sees a `TAP`. Raw coordinate tapping remains a fallback for non-text visual targets.

**Validation:** The independent Settings probe frame produced 18 numbered elements, including WLAN, Bluetooth, mobile network, display/brightness, sound/vibration, and notifications. The first attempt exposed a concrete integration bug: Tesseract's `tsv` config was searched inside the custom model directory and silently returned plain text. Explicit `tessedit_create_tsv=1` plus sparse-text page segmentation fixed the output. The marked evidence is preserved at `artifacts/probes/ui-tree/20260916T192635Z/shadow_marked.png`.

**Why:** This retains correct display alignment and user privacy while giving the model stable, inspectable targets. It avoids pretending that Display 0's accessibility tree describes the Agent display.

## 026 — Phone intelligence is replaceable; safety is not

**Decision:** Put phone-GUI models behind a provider-neutral client. Keep local Qwen3-VL 4B only as a development fallback and evaluate a phone-trained model such as AutoGLM-Phone-9B or UI-TARS through an OpenAI-compatible endpoint. Model output is parsed as `PhoneDecision`, never executed directly.

**Why:** The Bluetooth experiment proved that a generic 4B VLM can navigate to the correct page yet fail to interpret a simple switch and termination condition. This is a model-capability bottleneck, not evidence that more orchestration roles are needed.

## 027 — Query, mutation and sensitive intent are separate policy modes

**Decision:** Every mission receives a deterministic `TaskMode`. Read-only tasks must report observed state and may not change it; mutation tasks permit only contract-listed reversible operations; sensitive tasks fail closed or request takeover. Negated phrases such as “不要播放” and “不要下单” are treated as restrictions, not requested actions.

## 028 — Dashboard-visible artifacts are atomic

**Decision:** State, result, raw frame and marked-frame files are written to a sibling temporary file and atomically replaced. The dashboard must never parse or render a partially written artifact.

## 029 — Policy authorizes capabilities, not coordinates

**Decision:** Every model-proposed interaction declares a semantic capability. Read-only missions accept navigation, reading and search but deterministically reject setting changes, media control, writes and sensitive operations. Unknown click capability fails closed. Sensitive missions stop before target-app resolution or shadow-display creation.

**Why:** A coordinate alone cannot distinguish opening a Bluetooth page from toggling Bluetooth. Task mode in a prompt is guidance; a capability gate in code is enforcement.

## 030 — Device loss is infrastructure state, not a replan

**Decision:** Perform bounded ADB readiness recovery before execution and emit typed `DEVICE_LOST` when unavailable. Never spend planner replan budget or replay the last GUI action to recover infrastructure connectivity.

**Retry policy:** Replace the single permissive replan counter with deterministic failure classification and separate gentle versus sensitive budgets. A no-effect shadow action may re-observe and replan; app-launch repetition and every isolation violation remain fail-closed.

## 2026-09-17 — Heterogeneous live-task validation

**Bluetooth read-only query:** The reactive agent completed “查看蓝牙是否已开启” in two steps with zero replans. It independently reported `蓝牙状态=未开启`; verification passed. Isolation metrics remained `primary_display_actions=0`, `package_leaks=0`, `isolation_violations=0`, and `guard_violations=0`.

**Music authentication boundary:** For “打开网易云音乐，搜索歌曲《这就是爱》并播放”, the agent navigated from the observed screen and then returned `TAKE_OVER` when the app required a new login. Authentication markers such as expired login, password, SMS code, and account verification are now a deterministic human boundary; the model may not explore credential-recovery flows.

**Meituan generalization probe:** The agent generated a read-only contract specific to search and comparison, opened Meituan from the observed screen, and exposed two independent runtime defects rather than following the old route script: premature `REPORT` after completing only an app-open subgoal, and a model response using the OCR label `搜索` where the protocol required a numeric element id. `REPORT` is now rejected unless it covers a required task outcome, transient blank/loading verification is retryable, and a unique OCR text label is deterministically normalized to its element id. The subsequent run passed schema normalization but the local 8B model stalled during its next inference; the run was stopped without touching Display 0.

**Conclusion:** The runtime is now reactive and task-conditioned, not a hard-coded route. It is not yet production-general: end-to-end success still depends on model latency, visual grounding quality, and completing the full success-criteria loop across unfamiliar apps.

## 2026-09-17 — Explicit messaging and login handoff

Messages are permitted only when the submitted task itself names the recipient and contains the complete message body. The contract stores that exact instruction as `sensitive_scope`; navigation, search and text entry remain separately typed, while the final send control must use `SENSITIVE`. The Guard rejects altered message text and rejects any `SENSITIVE` action whose reason is not an explicit final send. QQ is deterministically resolved to `com.tencent.mobileqq` so the model cannot confuse it with WeChat or another Tencent package.

Live QQ probing reached the logged-in message list and found the requested contact, but OCR confused the conversation row with the profile header. Three runs were stopped before text entry or transmission; no message was sent. This validated the fail-closed policy and exposed the need for stronger row-level grounding before the feature can be called reliable.

Authentication pages now return `TAKE_OVER`. The Dashboard renders a direct instruction to log in on the phone and resubmit afterward; BearBless never reads or fills usernames, passwords, or verification codes.

## 2026-09-17 — Blank-frame handling and cloud-model path

The “nearest coffee” run showed two independent latencies: the local 8B model took roughly 57 seconds to choose one action, while the post-click frame was captured about 1.4 seconds later and was uniformly white (15 KB, all-white perceptual fingerprint). A cloud model can reduce decision latency and grounding errors, but cannot repair an Android Activity/WebView that has not rendered on the secondary display.

Uniform black/white frames are therefore classified deterministically before inference. BearBless waits and recaptures three times with increasing delays, then returns an explicit secondary-display rendering incompatibility error. Such frames are never sent to the planner and can no longer produce hallucinated success reports.

The immediate cloud migration path is the existing OpenAI-compatible adapter with `qwen3-vl-plus`. The preferred follow-up for phone grounding is a dedicated `gui-plus` adapter that translates its GUI action protocol into BearBless `PhoneDecision` objects while retaining the existing Guard and Verifier.
# 2026-09-17 — Display-scoped text entry without an IME

**Problem:** `adb shell input text` cannot safely enter Chinese on the verified
Huawei device and may activate the primary-display IME, violating the zero
interruption invariant.

**Decision:** Add a minimal Android Accessibility companion. The desktop calls
an exported `ContentProvider` through ADB shell; the provider accepts only UID
2000/root. The service enumerates accessibility windows by display, refuses
Display 0, and performs `ACTION_SET_TEXT` only on a unique (or uniquely
focused) editable node on the current shadow display.

**Fallback policy:** ASCII may still use display-targeted ADB input. Unicode
never falls back to ADB text, clipboard, or the primary-display keyboard.
Ambiguous/missing accessibility nodes fail closed.

**Verification status:** The APK was built and installed on the Huawei ELS-AN00
(Android 12 / API 31), the service health probe returned `enabled=true`, and
the Python suite passed. A real Quark shadow-display test established an
important boundary: Quark's custom search surface exposes no node supporting
`ACTION_SET_TEXT`, even after the search surface gains input focus. The bridge
correctly failed closed with `no editable node on display 71`; the primary IME
was nevertheless observed by the user even though a later `dumpsys` snapshot
reported `mIsInputViewShown=false`. That transient system value is not proof of
non-interference. Browser searches must therefore use a query URL; taps on
search/address/input surfaces are blocked deterministically. Custom app
surfaces need a verified deep link or takeover.
