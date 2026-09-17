from __future__ import annotations

import json
from pathlib import Path
import base64
import html

import streamlit as st
import streamlit.components.v1 as components

from bearbless.dashboard_data import TaskRequestError, compile_dynamic_mission_contract, submit_task_request
from bearbless.realtime_voice import ensure_realtime_voice_server
from bearbless.voice import VoiceTranscriptionError, to_simplified, transcribe_audio
from bearbless.worker import ensure_background_worker
from bearbless.config import Config


RUNS_ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "runs"
REQUESTS_ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "requests"
ICON = Path(__file__).resolve().parents[1] / "bearbless_icon_bear.svg"
REALTIME_SPEECH = components.declare_component(
    "bearbless_realtime_speech",
    path=str(Path(__file__).resolve().parent / "realtime_speech"),
)
ensure_background_worker(REQUESTS_ROOT)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def latest_run() -> Path | None:
    candidates = [path for path in RUNS_ROOT.iterdir() if path.is_dir()] if RUNS_ROOT.exists() else []
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def safe_text(value: object) -> str:
    return html.escape(str(value))


st.set_page_config(
    page_title="与熊同行 · BearBless",
    page_icon=str(ICON) if ICON.exists() else "🙏",
    layout="wide",
)

st.markdown("""
<style>
:root {
  --gb-bg: #000000;
  --gb-panel: #161617;
  --gb-border: rgba(255, 255, 255, .09);
  --gb-text: #f5f5f7;
  --gb-muted: #a1a1a6;
  --gb-purple: #a78bfa;
  --gb-gold: #ffd27a;
  --gb-green: #5ee0a5;
}
.stApp {
  background:
    radial-gradient(800px 440px at 50% -12%, rgba(123, 92, 255, .17), transparent 68%),
    var(--gb-bg);
  color: var(--gb-text);
  font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'SF Pro Text', 'PingFang SC', sans-serif;
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stToolbar"] { opacity: .45; }
.block-container { max-width: 1460px; padding-top: 2.2rem; padding-bottom: 7rem; }
.gb-hero {
  display: flex; flex-direction: column; align-items: center; text-align: center;
  gap: 20px; padding: 24px 4px 48px;
}
.gb-companion {
  position:relative; display:grid; place-items:center; width:210px; height:190px;
  isolation:isolate;
}
.gb-companion:before {
  content:''; position:absolute; inset:4px -34px -18px; z-index:-1; border-radius:50%;
  background:radial-gradient(ellipse at center,rgba(118,87,232,.30) 0%,rgba(91,70,210,.13) 35%,rgba(0,0,0,0) 72%);
  filter:blur(10px); animation:gbHalo 5.2s ease-in-out infinite;
}
.gb-companion:after {
  content:''; position:absolute; width:108px; height:18px; bottom:8px; z-index:-1;
  background:rgba(91,70,210,.22); filter:blur(15px); border-radius:50%;
}
.gb-hero img {
  width: 168px; height: 168px; padding: 0; border-radius: 38px;
  background: transparent; border: 0;
  filter: drop-shadow(0 30px 52px rgba(75,63,204,.30));
  transition:transform .35s cubic-bezier(.2,.8,.2,1),filter .35s ease;
}
.gb-companion:hover img { transform:translateY(-3px) scale(1.025); filter:drop-shadow(0 34px 62px rgba(100,78,235,.42)); }
@keyframes gbHalo { 0%,100%{opacity:.72;transform:scale(.96)} 50%{opacity:1;transform:scale(1.04)} }
@media (prefers-reduced-motion: reduce) { .gb-companion:before { animation:none; } .gb-hero img { transition:none; } }
.gb-eyebrow { color: var(--gb-gold); text-transform: uppercase; letter-spacing: .18em; font-size: .72rem; font-weight: 600; }
.gb-title { margin: 6px 0 8px; font-size: clamp(2.6rem, 5vw, 4.7rem); line-height: .98; letter-spacing: -.065em; font-weight: 700; }
.gb-subtitle { color: var(--gb-muted); font-size: clamp(1.05rem, 2vw, 1.35rem); line-height: 1.5; margin: 0; }
.gb-section { margin: 4.3rem 0 1.15rem; }
.gb-section-kicker { color: var(--gb-purple); font-size: .72rem; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }
.gb-section h2 { margin: .25rem 0 0; font-size: clamp(1.65rem, 3vw, 2.25rem); letter-spacing: -.04em; }
.gb-section p { color: var(--gb-muted); margin: .45rem 0 0; font-size: 1.02rem; }
[data-testid="stForm"] {
  background: var(--gb-panel); border: 1px solid var(--gb-border); border-radius: 24px;
  padding: 1.35rem 1.45rem 1.2rem; box-shadow: 0 22px 65px rgba(0,0,0,.32);
}
[data-testid="stTextInput"] input {
  background: #0a0a0b; border: 1px solid rgba(255,255,255,.13); border-radius: 13px;
  color: var(--gb-text); min-height: 52px;
}
[data-testid="stFormSubmitButton"] button {
  border: 0; border-radius: 999px; min-height: 46px; padding-inline: 1.4rem; font-weight: 600; color: white;
  background: #7657e8; box-shadow: none;
}
[data-testid="stFormSubmitButton"] button:hover { background: #876bf0; transform: translateY(-1px); }
[data-testid="stMetric"] {
  height: 100%; background: var(--gb-panel); border: 1px solid var(--gb-border); border-radius: 20px;
  padding: 17px 18px; box-shadow: none;
}
[data-testid="stMetricLabel"] { color: var(--gb-muted); }
[data-testid="stMetricValue"] { color: var(--gb-text); font-size: 1.75rem; }
[data-testid="stAlert"] { border-radius: 14px; border: 1px solid rgba(83,221,165,.20); background: rgba(25,87,66,.18); }
[data-testid="stImage"] img { border-radius: 18px; border: 1px solid var(--gb-border); box-shadow: 0 20px 50px rgba(0,0,0,.28); }
[data-testid="stProgress"] > div > div { background: linear-gradient(90deg, var(--gb-purple), var(--gb-gold)); }
[data-testid="stDataFrame"] { border: 1px solid var(--gb-border); border-radius: 16px; overflow: hidden; }
.gb-status {
  display: inline-flex; align-items: center; gap: 8px; padding: 7px 11px; border-radius: 999px;
  color: var(--gb-green); background: rgba(83,221,165,.10); border: 1px solid rgba(83,221,165,.20);
  font-size: .8rem; font-weight: 700; letter-spacing: .05em;
}
.gb-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--gb-green); box-shadow: 0 0 14px var(--gb-green); }
.gb-card {
  padding: 18px 20px; border-radius: 18px; background: var(--gb-panel); border: 1px solid var(--gb-border);
}
.gb-live-head { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:14px; }
.gb-live-badge {
  display:inline-flex; align-items:center; gap:8px; padding:7px 11px; border-radius:999px;
  background:rgba(94,224,165,.09); border:1px solid rgba(94,224,165,.18); color:var(--gb-green);
  font-size:.75rem; font-weight:700; letter-spacing:.08em;
}
.gb-pulse { width:7px; height:7px; border-radius:50%; background:var(--gb-green); animation:gbPulse 1.8s infinite; }
@keyframes gbPulse { 0%,100%{box-shadow:0 0 0 0 rgba(94,224,165,.32)} 50%{box-shadow:0 0 0 7px rgba(94,224,165,0)} }
.gb-fsm { display:flex; flex-wrap:wrap; gap:7px; margin:12px 0 4px; }
.gb-phase { color:#73737a; background:#0b0b0c; border:1px solid rgba(255,255,255,.07); border-radius:999px; padding:6px 10px; font-size:.69rem; font-weight:650; }
.gb-phase.active { color:white; background:#7657e8; border-color:#8d73ec; box-shadow:0 6px 20px rgba(118,87,232,.24); }
.gb-action-row { display:grid; grid-template-columns:76px 1fr auto; gap:12px; align-items:center; padding:11px 0; border-bottom:1px solid rgba(255,255,255,.07); }
.gb-action-row:last-child { border-bottom:0; }
.gb-action-type { color:var(--gb-purple); font-size:.72rem; font-weight:700; letter-spacing:.06em; }
.gb-action-reason { color:#e8e8ed; font-size:.88rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.gb-action-result { color:var(--gb-muted); font-size:.72rem; }
.gb-screen-placeholder { min-height:330px; display:grid; place-items:center; text-align:center; color:var(--gb-muted); background:#09090a; border:1px solid var(--gb-border); border-radius:24px; }
.gb-safety-island { display:flex; justify-content:center; margin:8px 0 22px; }
.gb-safety-pill { display:inline-flex; align-items:center; gap:10px; padding:10px 15px; color:#d9fbea; background:rgba(34,89,66,.32); border:1px solid rgba(94,224,165,.22); border-radius:999px; font-size:.78rem; backdrop-filter:blur(18px); }
.gb-safety-pill strong { color:white; font-weight:650; }
.gb-panel-title { margin:0 0 14px; color:#8e8e93; font-size:.69rem; font-weight:700; letter-spacing:.14em; text-transform:uppercase; }
.gb-plan { display:flex; flex-direction:column; gap:4px; }
.gb-plan-step { position:relative; padding:10px 10px 10px 31px; color:#77777e; font-size:.84rem; border-radius:11px; }
.gb-plan-step:before { content:''; position:absolute; left:10px; top:16px; width:8px; height:8px; border:1px solid #56565d; border-radius:50%; }
.gb-plan-step.done { color:#c9c9ce; }
.gb-plan-step.done:before { background:var(--gb-green); border-color:var(--gb-green); box-shadow:0 0 10px rgba(94,224,165,.3); }
.gb-plan-step.active { color:white; background:rgba(118,87,232,.13); }
.gb-plan-step.active:before { background:var(--gb-purple); border-color:var(--gb-purple); box-shadow:0 0 0 5px rgba(167,139,250,.1); }
.gb-phone { max-width:390px; margin:auto; padding:10px; border-radius:42px; background:linear-gradient(145deg,#343438,#0b0b0c 34%); border:1px solid rgba(255,255,255,.17); box-shadow:0 35px 90px rgba(0,0,0,.58), inset 0 0 0 1px rgba(255,255,255,.05); }
.gb-phone-screen { min-height:485px; display:grid; place-items:center; overflow:hidden; border-radius:33px; background:radial-gradient(circle at 50% 35%,rgba(118,87,232,.16),transparent 36%),#080809; border:1px solid rgba(255,255,255,.07); }
.gb-phone-idle { text-align:center; color:#74747c; }
.gb-phone-idle .orb { width:48px; height:48px; margin:0 auto 15px; border-radius:50%; background:radial-gradient(circle at 35% 30%,#c4b5fd,#7657e8 55%,#281b67); box-shadow:0 0 40px rgba(118,87,232,.34); animation:gbFloat 3s ease-in-out infinite; }
@keyframes gbFloat { 50%{transform:translateY(-5px);box-shadow:0 8px 48px rgba(118,87,232,.46)} }
.gb-current-action { padding:17px; border-radius:17px; background:linear-gradient(145deg,rgba(118,87,232,.16),rgba(22,22,23,.92)); border:1px solid rgba(167,139,250,.18); }
.gb-action-label { color:var(--gb-purple); font-size:.67rem; font-weight:750; letter-spacing:.13em; }
.gb-action-main { color:white; font-size:1.02rem; font-weight:630; line-height:1.35; margin:8px 0; }
.gb-action-meta { color:#8e8e93; font-size:.76rem; line-height:1.5; }
.gb-timeline { display:flex; align-items:center; gap:0; margin:22px 2px 4px; }
.gb-time-node { width:9px; height:9px; flex:0 0 auto; border-radius:50%; background:#343438; border:1px solid #55555b; }
.gb-time-node.done { background:var(--gb-purple); border-color:#b5a1fa; box-shadow:0 0 9px rgba(167,139,250,.42); }
.gb-time-line { height:1px; flex:1; background:#343438; }
.gb-time-line.done { background:rgba(167,139,250,.52); }
.gb-time-caption { display:flex; justify-content:space-between; color:#68686f; font-size:.68rem; margin-top:8px; }
.gb-detail-row { display:flex; justify-content:space-between; gap:12px; padding:11px 0; border-bottom:1px solid rgba(255,255,255,.06); font-size:.79rem; }
.gb-detail-row:last-child { border:0; }
.gb-detail-row span:first-child { color:#8e8e93; }
.gb-detail-row span:last-child { color:#e5e5ea; text-align:right; }
.gb-contract { margin:16px 0 4px; padding:20px; border-radius:20px; background:linear-gradient(145deg,rgba(118,87,232,.13),rgba(22,22,23,.96) 42%); border:1px solid rgba(167,139,250,.2); }
.gb-contract-head { display:flex; justify-content:space-between; align-items:center; gap:14px; margin-bottom:15px; }
.gb-contract-title { color:white; font-size:1.02rem; font-weight:680; }
.gb-contract-chip { padding:6px 9px; border-radius:999px; color:var(--gb-green); background:rgba(94,224,165,.08); border:1px solid rgba(94,224,165,.17); font-size:.68rem; font-weight:700; letter-spacing:.08em; }
.gb-contract-grid { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; }
.gb-contract-block { padding:13px; border-radius:13px; background:rgba(0,0,0,.22); border:1px solid rgba(255,255,255,.06); }
.gb-contract-block b { display:block; margin-bottom:8px; color:#8e8e93; font-size:.67rem; letter-spacing:.1em; text-transform:uppercase; }
.gb-contract-block div { color:#d9d9de; font-size:.78rem; line-height:1.65; }
.gb-receipt { position:relative; padding:24px; border-radius:22px; background:linear-gradient(155deg,#1b1b1d,#101011); border:1px solid rgba(94,224,165,.18); overflow:hidden; }
.gb-receipt:after { content:'✓'; position:absolute; right:18px; top:8px; color:rgba(94,224,165,.08); font-size:7rem; font-weight:800; }
.gb-receipt-head { color:var(--gb-green); font-size:.7rem; font-weight:750; letter-spacing:.14em; }
.gb-receipt-title { margin:7px 0 17px; color:white; font-size:1.35rem; font-weight:680; }
.gb-proof-answer { padding:16px; border-radius:16px; background:#111112; border:1px solid rgba(255,255,255,.08); }
[data-testid="stExpander"] { border:1px solid var(--gb-border); border-radius:18px; background:rgba(22,22,23,.72); }
hr { border-color: var(--gb-border) !important; }
@media (max-width: 720px) {
  .block-container { padding: 2rem 1rem 4rem; }
  .gb-hero { gap: 16px; padding: 12px 0 36px; }
  .gb-companion { width:170px; height:154px; }
  .gb-hero img { width:142px; height:142px; border-radius:32px; }
  .gb-title { font-size: 3rem; }
  .gb-section { margin-top: 3.2rem; }
  .gb-phone-screen { min-height:400px; }
  .gb-contract-grid { grid-template-columns:1fr; }
}
</style>
""", unsafe_allow_html=True)

icon_data = ""
if ICON.exists():
    icon_data = base64.b64encode(ICON.read_bytes()).decode("ascii")
st.markdown(f"""
<div class="gb-hero">
  <div class="gb-companion"><img src="data:image/svg+xml;base64,{icon_data}" alt="BearBless bear icon" /></div>
  <div>
    <div class="gb-eyebrow">Non-interruptive mobile agent</div>
    <div class="gb-title">与熊同行 <span style="color:#9b7cff">·</span> BearBless</div>
    <p class="gb-subtitle">你拥有手机主屏，Agent 只在不可见的平行空间工作。</p>
  </div>
</div>
""", unsafe_allow_html=True)

st.markdown("<div class='gb-section'><div class='gb-section-kicker'>语音或文字</div><h2>告诉小熊你想做什么</h2><p>点击麦克风即可实时看到简体中文听写；停止后自动填入任务框，不会直接触发手机操作。</p></div>", unsafe_allow_html=True)
speech_result = REALTIME_SPEECH(
    language="zh-CN",
    initial_text=st.session_state.get("task_goal", ""),
    endpoint=ensure_realtime_voice_server(),
    key="realtime_speech",
    default=None,
)
if isinstance(speech_result, dict) and speech_result.get("transcript"):
    nonce = speech_result.get("nonce")
    if nonce != st.session_state.get("speech_nonce"):
        st.session_state["speech_nonce"] = nonce
        st.session_state["task_goal"] = to_simplified(str(speech_result["transcript"]))
        st.success("实时听写已完成，请确认或修改后再生成任务契约。")

with st.expander("浏览器不支持实时听写？使用本地录音后转写"):
    voice = st.audio_input("录制任务语音", sample_rate=16000)
    if st.button("使用本地 Whisper 转成文字", disabled=voice is None):
        try:
            with st.spinner("小熊正在本地听写…"):
                suffix = Path(voice.name).suffix or ".wav"
                st.session_state["task_goal"] = transcribe_audio(voice.getvalue(), suffix=suffix)
            st.success("已转成简体中文，请确认或修改。")
        except VoiceTranscriptionError as exc:
            st.error(str(exc))

with st.form("task_request"):
    input_col, submit_col = st.columns([5, 1], vertical_alignment="bottom")
    with input_col:
        goal = st.text_input(
            "任务文字",
            key="task_goal",
            placeholder="例如：比较哥本哈根到汉堡的出行方案，并把推荐结果保存到记事本",
        )
    with submit_col:
        reviewed = st.form_submit_button("生成任务契约 →", type="primary", width="stretch")
    if reviewed:
        try:
            with st.spinner("小熊正在理解任务、分析权限和完成条件…"):
                st.session_state["mission_contract"] = compile_dynamic_mission_contract(goal).model_dump(mode="json")
                st.session_state["mission_contract_version"] = 2
        except TaskRequestError as exc:
            st.error(str(exc))

if st.session_state.get("mission_contract_version") != 2:
    st.session_state.pop("mission_contract", None)
contract_payload = st.session_state.get("mission_contract")
if contract_payload:
    allowed = "".join(f"<div>✓ {safe_text(item)}</div>" for item in contract_payload.get("allowed_actions", []))
    forbidden = "".join(f"<div>× {safe_text(item)}</div>" for item in contract_payload.get("forbidden_actions", []))
    criteria = "".join(f"<div>○ {safe_text(item['description'])}</div>" for item in contract_payload.get("success_criteria", []))
    st.markdown(
        f'<div class="gb-contract"><div class="gb-contract-head"><div class="gb-contract-title">Mission Contract · {safe_text(contract_payload["goal"])}</div>'
        f'<div class="gb-contract-chip">INTERRUPTION BUDGET · {safe_text(contract_payload.get("interruption_budget", 0))}</div></div>'
        f'<div class="gb-contract-grid"><div class="gb-contract-block"><b>允许</b>{allowed}</div>'
        f'<div class="gb-contract-block"><b>禁止</b>{forbidden}</div>'
        f'<div class="gb-contract-block"><b>完成条件</b>{criteria}</div></div></div>',
        unsafe_allow_html=True,
    )
    cloud_provider = Config.load().phone_model_provider != "ollama"
    cloud_consent = True
    if cloud_provider:
        cloud_consent = st.checkbox(
            "我同意本次任务将 Agent 虚拟屏截图上传至阿里云百炼，用于 GUI 决策与结果验证",
            value=False,
        )
        st.caption("只上传隔离虚拟屏；不上传用户主屏、系统剪贴板、账号密码或验证码。")
    if st.button("确认边界并静默执行", type="primary", width="stretch", disabled=not cloud_consent):
        from bearbless.schemas import TaskSpec
        contract = TaskSpec.model_validate(contract_payload)
        if cloud_provider:
            contract = contract.model_copy(update={
                "constraints": {**contract.constraints, "cloud_vision_consent": True}
            })
        request_path = submit_task_request(contract.goal, REQUESTS_ROOT, contract)
        st.success(f"任务已进入队列：{request_path.stem}。现在无需继续操作 Dashboard。")
        del st.session_state["mission_contract"]

request_files = sorted(REQUESTS_ROOT.glob("request-*.json"), key=lambda path: path.stat().st_mtime, reverse=True) if REQUESTS_ROOT.exists() else []
if request_files:
    latest_request = read_json(request_files[0])
    st.caption(
        f"Latest request: {latest_request.get('request_id')} · "
        f"{latest_request.get('status')}"
        + (f" · task {latest_request.get('task_id')}" if latest_request.get("task_id") else "")
    )


def event_reason(event: dict) -> str:
    payload = event.get("payload", {})
    return str(payload.get("reason") or payload.get("action") or event.get("type", "等待下一步"))


def human_failure_reason(state: dict) -> str:
    failures = state.get("collected_data", {}).get("recoverable_failures", [])
    detail = str(failures[-1].get("detail", "")) if failures else ""
    reason = str(state.get("failure_reason") or detail or "任务未完成")
    if "replan budget exhausted" in reason:
        if "unsupported GUI-Plus action" in detail:
            return f"模型动作格式暂不兼容：{detail}"
        return f"连续尝试后仍无法安全继续。最后原因：{detail or reason}"
    if "截图为空" in reason or "空白" in reason:
        return "虚拟屏没有渲染出可验证内容，任务已安全停止。"
    return reason


@st.fragment(run_every="1s")
def render_live_workbench() -> None:
    newest_requests = sorted(
        REQUESTS_ROOT.glob("request-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if REQUESTS_ROOT.exists() else []
    newest_request = read_json(newest_requests[0]) if newest_requests else {}
    if newest_request.get("status") == "FAILED" and not newest_request.get("task_id"):
        st.error(
            f"任务未启动：{newest_request.get('error') or '启动前检查失败'}",
            icon="⚠️",
        )
        return
    live_run = latest_run()
    if live_run is None:
        return
    live_state = read_json(live_run / "state.json")
    live_metrics = read_json(live_run / "metrics.json")
    events_path = live_run / "events.jsonl"
    live_events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()] if events_path.exists() else []
    live_frames = (
        sorted(path for path in (live_run / "screens").glob("frame_*.png") if not path.stem.endswith("_marked"))
        if (live_run / "screens").exists() else []
    )
    status = live_state.get("status", "PENDING")
    step_index = int(live_state.get("step_index", 0))
    current_reason = event_reason(live_events[-1]) if live_events else (live_state.get("current_subgoal") or "等待下一步")
    display_zero = live_metrics.get("agent_actions_targeting_primary_display", 0)
    violations = live_metrics.get("isolation_violations", 0)
    safe = display_zero == 0 and violations == 0

    failure_reason = str(live_state.get("failure_reason") or "")
    takeover = live_state.get("collected_data", {}).get("human_takeover")
    if isinstance(takeover, dict) and takeover.get("kind") == "verification":
        st.warning(
            "需要人工验证：请在电脑上的 BearBless Shadow Display 窗口手动完成滑块；完成后 Agent 会自动继续。",
            icon="🖐️",
        )
    terminal_notice_key = f"{live_run.name}:{status}"
    if status == "COMPLETED":
        summary = str(live_state.get("collected_data", {}).get("agent_result") or "任务已完成并通过验证")
        st.success(f"任务完成：{summary}", icon="✅")
        if st.session_state.get("terminal_notice") != terminal_notice_key:
            st.toast(f"BearBless 已完成：{summary}", icon="✅")
            st.session_state["terminal_notice"] = terminal_notice_key
    elif status == "FAILED":
        readable_failure = human_failure_reason(live_state)
        st.error(f"任务未完成：{readable_failure}", icon="⚠️")
        current_reason = "任务已停止，不会继续执行"
        if st.session_state.get("terminal_notice") != terminal_notice_key:
            st.toast(f"任务已停止：{readable_failure}", icon="⚠️")
            st.session_state["terminal_notice"] = terminal_notice_key
    else:
        st.info(f"任务执行中 · 第 {step_index} 步 · {current_reason}", icon="🐻")
    if "TAKE_OVER" in failure_reason and any(
        marker in failure_reason for marker in ("登录", "身份验证", "验证码", "密码")
    ):
        st.warning("需要你登录：请在手机上完成登录或身份验证，然后重新提交任务。BearBless 不会读取或代填账号、密码和验证码。")

    st.markdown(
        f'<div class="gb-safety-island"><div class="gb-safety-pill"><span class="gb-pulse"></span>'
        f'<strong>{"安全隔离中" if safe else "检测到隔离异常"}</strong><span>·</span>'
        f'<span>主屏操作 {safe_text(display_zero)}</span><span>·</span><span>违规 {safe_text(violations)}</span></div></div>',
        unsafe_allow_html=True,
    )

    plan_col, phone_col, detail_col = st.columns([.78, 1.34, .88], gap="large")
    with plan_col:
        st.markdown('<div class="gb-panel-title">任务计划</div>', unsafe_allow_html=True)
        plan_labels = [
            "理解并规划任务",
            "打开目标应用",
            "观察虚拟屏内容",
            "执行当前操作",
            "任务已停止" if status == "FAILED" else "验证最终结果",
        ]
        if status in {"COMPLETED", "FAILED"}:
            active_plan = len(plan_labels) - 1
        else:
            active_plan = min(max(step_index, 0), len(plan_labels) - 1)
        plan_html = ""
        for index, label in enumerate(plan_labels):
            css = "done" if (status == "COMPLETED" or index < active_plan) else "active" if index == active_plan else ""
            plan_html += f'<div class="gb-plan-step {css}">{safe_text(label)}</div>'
        st.markdown(f'<div class="gb-plan">{plan_html}</div>', unsafe_allow_html=True)
        st.markdown('<div class="gb-panel-title" style="margin-top:30px">运行信息</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="gb-card"><div class="gb-detail-row"><span>任务</span><span>{safe_text(live_run.name)}</span></div>'
            f'<div class="gb-detail-row"><span>步骤</span><span>{safe_text(step_index)}</span></div>'
            f'<div class="gb-detail-row"><span>虚拟屏</span><span>{safe_text(live_state.get("shadow_display_id", "—"))}</span></div></div>',
            unsafe_allow_html=True,
        )

    with phone_col:
        st.markdown('<div class="gb-panel-title" style="text-align:center">Agent 虚拟手机</div>', unsafe_allow_html=True)
        if live_frames:
            selected_frame = len(live_frames) - 1
            if len(live_frames) > 1:
                frame_labels = []
                action_history = live_state.get("action_history", [])
                for index, frame in enumerate(live_frames):
                    try:
                        step_number = int(frame.stem.rsplit("_", 1)[-1])
                    except ValueError:
                        step_number = index + 1
                    action = action_history[step_number - 1] if 0 < step_number <= len(action_history) else {}
                    reason = action.get("reason") or "捕获虚拟屏"
                    frame_labels.append(f"{index + 1}/{len(live_frames)} · {reason}")
                selected_frame = st.select_slider(
                    "任务过程回放",
                    options=range(len(live_frames)),
                    value=len(live_frames) - 1,
                    format_func=lambda index: frame_labels[index],
                )
            st.image(str(live_frames[selected_frame]), caption=f"Shadow display · {live_frames[selected_frame].name}", width="stretch")
        else:
            st.markdown('<div class="gb-phone"><div class="gb-phone-screen"><div class="gb-phone-idle"><div class="orb"></div><b>等待 Agent 虚拟屏</b><br><small>连接手机后将在这里实时出现</small></div></div></div>', unsafe_allow_html=True)

        node_count = max(min(len(live_events), 8), 2)
        timeline = "".join(
            ('<span class="gb-time-node done"></span>' if i < node_count - 1 else '<span class="gb-time-node"></span>')
            + ('<span class="gb-time-line done"></span>' if i < node_count - 1 else "")
            for i in range(node_count)
        )
        st.markdown(
            f'<div class="gb-timeline">{timeline}</div><div class="gb-time-caption"><span>任务开始</span>'
            f'<span>{"已完成" if status == "COMPLETED" else "未完成" if status == "FAILED" else "● LIVE"}</span></div>',
            unsafe_allow_html=True,
        )

    with detail_col:
        st.markdown('<div class="gb-panel-title">当前动作</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="gb-current-action"><div class="gb-action-label">{safe_text(status)}</div>'
            f'<div class="gb-action-main">{safe_text(current_reason)}</div>'
            f'<div class="gb-action-meta">{safe_text(live_state.get("current_subgoal") or "Runtime 正在等待新的任务状态")}</div></div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="gb-panel-title" style="margin-top:28px">最近活动</div>', unsafe_allow_html=True)
        recent = live_events[-5:]
        if not recent:
            st.markdown('<div class="gb-card">等待 Runtime 事件…</div>', unsafe_allow_html=True)
        else:
            rows = []
            for event in reversed(recent):
                payload = event.get("payload", {})
                reason = payload.get("reason") or payload.get("action") or event.get("type", "event")
                rows.append(
                    f'<div class="gb-action-row" style="grid-template-columns:64px 1fr"><div class="gb-action-type">{safe_text(event.get("type", "EVENT")).upper()}</div>'
                    f'<div class="gb-action-reason">{safe_text(reason)}</div>'
                    f'</div>'
                )
            st.markdown(f'<div class="gb-card">{"".join(rows)}</div>', unsafe_allow_html=True)


render_live_workbench()

run = latest_run()
if run is None:
    st.info("No task artifacts yet. Run `python -m bearbless demo-fixture`.")
    st.stop()

state = read_json(run / "state.json")
metrics = read_json(run / "metrics.json")
result = read_json(run / "result.json")

events_path = run / "events.jsonl"
events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()] if events_path.exists() else []
evidence = result.get("evidence", state.get("evidence", []))
frames = (
    sorted(path for path in (run / "screens").glob("frame_*.png") if not path.stem.endswith("_marked"))
    if (run / "screens").exists() else []
)

st.markdown("<div class='gb-section'><div class='gb-section-kicker'>Proof Lens</div><h2>为什么这样做？</h2><p>选择任意一步，查看动作目的、执行结果和对应证据。</p></div>", unsafe_allow_html=True)
if events:
    proof_labels = [f"{index + 1:02d} · {event.get('type', 'event').upper()} · {event_reason(event)}" for index, event in enumerate(events)]
    proof_index = st.selectbox("选择一个 Agent 步骤", range(len(events)), format_func=lambda index: proof_labels[index])
    proof_event = events[proof_index]
    expected = None
    action_history = state.get("action_history", [])
    if proof_index < len(action_history):
        expected = action_history[proof_index].get("expected_outcome")
    observed = evidence[min(proof_index, len(evidence) - 1)] if evidence else {}
    proof_a, proof_b, proof_c = st.columns(3, gap="medium")
    with proof_a:
        st.markdown(
            f'<div class="gb-proof-answer"><div class="gb-panel-title">动作意图</div><b>{safe_text(event_reason(proof_event))}</b>'
            f'<div class="gb-action-meta" style="margin-top:8px">动作类型 · {safe_text(proof_event.get("type", "event")).upper()}</div></div>',
            unsafe_allow_html=True,
        )
    with proof_b:
        expected_text = expected.get("description") if isinstance(expected, dict) else "完成该步骤，并保持主屏零操作"
        st.markdown(
            f'<div class="gb-proof-answer"><div class="gb-panel-title">预期结果</div><b>{safe_text(expected_text)}</b>'
            f'<div class="gb-action-meta" style="margin-top:8px">Display · {safe_text(proof_event.get("display_id") or state.get("shadow_display_id", "shadow"))}</div></div>',
            unsafe_allow_html=True,
        )
    with proof_c:
        observed_text = observed.get("criterion") or observed.get("evidence") or proof_event.get("result", "recorded")
        st.markdown(
            f'<div class="gb-proof-answer"><div class="gb-panel-title">观察证据</div><b>{safe_text(observed_text)}</b>'
            f'<div class="gb-action-meta" style="margin-top:8px">结果 · {safe_text(proof_event.get("result", "recorded")).upper()}</div></div>',
            unsafe_allow_html=True,
        )
st.markdown("<div class='gb-section'><div class='gb-section-kicker'>Outcome</div><h2>任务结果</h2><p>结果与验证证据默认保持简洁，需要时可以继续展开。</p></div>", unsafe_allow_html=True)
result_col, proof_col = st.columns([1, 1], gap="large")
with result_col:
    st.markdown('<div class="gb-panel-title">推荐结果</div>', unsafe_allow_html=True)
    st.json(state.get("collected_data", {}).get("recommendation", {}), expanded=True)
with proof_col:
    st.markdown('<div class="gb-panel-title">验证摘要</div>', unsafe_allow_html=True)
    st.metric("通过的验证项", len(evidence))
    st.caption(f"Agent 操作 {metrics.get('agent_actions_total', 0)} · 主屏操作 {metrics.get('agent_actions_targeting_primary_display', 0)} · Replan {metrics.get('replans', 0)}")

st.markdown("<div class='gb-section'><div class='gb-section-kicker'>Completion Receipt</div><h2>完成与隐私回执</h2><p>结果之外，同时交付这次任务的权限与隔离记录。</p></div>", unsafe_allow_html=True)
receipt_status = state.get("status", "UNKNOWN")
receipt_rows = (
    ("Agent 操作", metrics.get("agent_actions_total", 0)),
    ("主屏操作", metrics.get("agent_actions_targeting_primary_display", 0)),
    ("隔离违规", metrics.get("isolation_violations", 0)),
    ("输入法违规", metrics.get("ime_policy_violations", 0)),
    ("系统剪贴板自动同步", "关闭" if metrics.get("clipboard_autosync_enabled") is False else "开启/未知"),
    ("独立验证", metrics.get("verification_attempts", 0)),
    ("Replan", metrics.get("replans", 0)),
    ("模型调用", metrics.get("model_calls_total", 0)),
    ("视觉调用", metrics.get("model_image_calls", 0)),
    ("高清视觉调用", metrics.get("model_high_res_calls", 0)),
    ("模型总耗时", f"{metrics.get('model_latency_ms', 0) / 1000:.1f}s"),
    ("登录接管提醒", metrics.get("user_attention_notifications", 0)),
    ("人机验证接管", metrics.get("human_verification_takeovers", 0)),
    ("执行空间", f"Shadow Display {state.get('shadow_display_id', '—')}"),
)
receipt_html = "".join(f'<div class="gb-detail-row"><span>{safe_text(label)}</span><span>{safe_text(value)}</span></div>' for label, value in receipt_rows)
st.markdown(
    f'<div class="gb-receipt"><div class="gb-receipt-head">BEARBLESS · VERIFIED RECEIPT</div>'
    f'<div class="gb-receipt-title">{safe_text(receipt_status)} · {safe_text(state.get("goal", "Task"))}</div>'
    f'<div style="max-width:720px">{receipt_html}</div></div>',
    unsafe_allow_html=True,
)

with st.expander("查看验证证据与开发者 Trace"):
    st.markdown("#### 完成证据")
    st.dataframe([
        {**item, "evidence": json.dumps(item.get("evidence"), ensure_ascii=False) if isinstance(item.get("evidence"), (dict, list)) else item.get("evidence")}
        for item in evidence
    ], width="stretch")
    st.markdown("#### Runtime 事件")
    st.dataframe([
        {**event, "payload": json.dumps(event.get("payload", {}), ensure_ascii=False)} for event in events
    ], width="stretch")
