from __future__ import annotations

import json
from pathlib import Path
import base64
import html
import time

import streamlit as st
import streamlit.components.v1 as components

from bearbless.dashboard_data import PRODUCT_CAPABILITIES, TaskRequestError, build_local_task_analysis, compile_dynamic_mission_contract, submit_task_request
from bearbless.realtime_voice import ensure_realtime_voice_server
from bearbless.voice import VoiceTranscriptionError, to_simplified, transcribe_audio
from bearbless.task_queue import request_cancellation
from bearbless.config import Config


RUNS_ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "runs"
REQUESTS_ROOT = Path(__file__).resolve().parents[1] / "artifacts" / "requests"
ICON = Path(__file__).resolve().parents[1] / "bearbless_icon_bear.svg"
APP_ICONS_ROOT = Path(__file__).resolve().parent / "assets" / "app_icons"
# The web projection is evidence-frame based rather than a native SDL window.
# A tighter fragment interval keeps newly captured Agent frames close to the
# separately rendered scrcpy window without refreshing the whole dashboard.
LIVE_REFRESH_SECONDS = 0.15
REALTIME_SPEECH = components.declare_component(
    "bearbless_realtime_speech",
    path=str(Path(__file__).resolve().parent / "realtime_speech"),
)

# Successful tasks briefly show their receipt and then return to the clean
# input state. Failed/stopped tasks remain visible until the user explicitly
# clears them. This flag is consumed before widgets are created because
# Streamlit forbids mutating a widget key afterwards.
if st.session_state.pop("reset_after_terminal", False) or st.session_state.pop("reset_after_completion", False):
    st.session_state["task_goal"] = ""
    st.session_state.pop("mission_contract", None)
    st.session_state.pop("mission_contract_version", None)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def latest_run() -> Path | None:
    candidates = [path for path in RUNS_ROOT.iterdir() if path.is_dir()] if RUNS_ROOT.exists() else []
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def safe_text(value: object) -> str:
    return html.escape(str(value))


def render_notice(kind: str, title: str, detail: str = "") -> None:
    icons = {"success": "✓", "error": "!", "info": "i", "warning": "!"}
    detail_html = f'<div class="gb-notice-detail">{safe_text(detail)}</div>' if detail else ""
    st.markdown(
        f'<div class="gb-notice {safe_text(kind)}">'
        f'<div class="gb-notice-icon">{icons.get(kind, "i")}</div>'
        f'<div class="gb-notice-copy"><div class="gb-notice-title">{safe_text(title)}</div>{detail_html}</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def image_data_uri(path: Path) -> str:
    mime_type = {
        ".png": "image/png",
        ".svg": "image/svg+xml",
    }.get(path.suffix.lower(), "image/jpeg")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


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
[data-testid="stAlert"] p, [data-testid="stAlert"] div { color:#202235 !important; }
[data-testid="stAlert"] svg { color:#6454d8 !important; fill:#6454d8 !important; }
[data-testid="stToast"] { background:rgba(255,255,255,.96) !important; border:1px solid rgba(112,87,232,.12) !important; box-shadow:0 16px 42px rgba(66,53,132,.14) !important; color:#242438 !important; }
[data-testid="stToast"] div, [data-testid="stToast"] p { color:#242438 !important; }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color:#626577 !important; }
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
.gb-app-grid { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; margin:0 0 20px; }
.gb-app-card { position:relative; min-height:196px; padding:18px; border-radius:24px; overflow:hidden;
  background:linear-gradient(155deg,rgba(31,31,34,.98),rgba(13,13,14,.98)); border:1px solid var(--gb-border); }
.gb-app-card:after { content:''; position:absolute; width:130px; height:130px; right:-55px; top:-58px;
  border-radius:50%; background:var(--app-glow); filter:blur(18px); opacity:.38; }
.gb-app-head { display:flex; align-items:center; gap:12px; position:relative; z-index:1; }
.gb-app-icon { width:50px; height:50px; flex:0 0 auto; display:grid; place-items:center; border-radius:15px;
  overflow:hidden; background:var(--app-color); box-shadow:0 12px 30px var(--app-shadow); }
.gb-app-icon img { width:100%; height:100%; display:block; object-fit:cover; }
.gb-app-name { color:white; font-size:1rem; font-weight:700; }
.gb-app-route { color:#8e8e93; font-size:.68rem; margin-top:3px; letter-spacing:.04em; }
.gb-app-copy { position:relative; z-index:1; min-height:42px; margin:15px 0 13px; color:#c7c7cc; font-size:.78rem; line-height:1.55; }
.gb-app-proof { position:relative; z-index:1; display:flex; align-items:center; gap:7px; color:#a8e8c8;
  font-size:.7rem; padding-top:11px; border-top:1px solid rgba(255,255,255,.07); }
.gb-app-proof:before { content:'✓'; display:grid; place-items:center; width:17px; height:17px; border-radius:50%;
  color:#08150f; background:var(--gb-green); font-size:.65rem; font-weight:900; }
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
.gb-phone-logo { width:82px; height:82px; margin:0 auto 18px; padding:5px; border-radius:24px; background:rgba(255,255,255,.72); border:1px solid rgba(112,87,232,.12); box-shadow:0 18px 42px rgba(90,70,190,.18); animation:gbFloat 3.4s ease-in-out infinite; }
.gb-phone-logo img { display:block; width:100%; height:100%; border-radius:19px; }
.gb-phone-idle b { display:block; color:#29283d; font-size:1rem; letter-spacing:-.015em; }
.gb-phone-idle small { display:block; margin-top:7px; color:#8c8b9d; font-size:.78rem; }
@keyframes gbFloat { 50%{transform:translateY(-5px);box-shadow:0 23px 50px rgba(90,70,190,.24)} }
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
  .gb-app-grid { grid-template-columns:1fr 1fr; }
}
@media (min-width:721px) and (max-width:1100px) {
  .gb-app-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
}
@media (max-width: 460px) { .gb-app-grid { grid-template-columns:1fr; } }

/* Light product language derived from the BearBless launch visual. */
:root {
  --gb-bg:#f7f8fc; --gb-panel:rgba(255,255,255,.78); --gb-border:rgba(60,54,112,.10);
  --gb-text:#17172a; --gb-muted:#6e7184; --gb-purple:#7057e8; --gb-gold:#8d75f4; --gb-green:#2dbb79;
}
.stApp {
  background:
    radial-gradient(900px 540px at 78% -5%,rgba(118,87,232,.16),transparent 68%),
    radial-gradient(740px 520px at -8% 40%,rgba(67,181,238,.10),transparent 72%),
    linear-gradient(180deg,#fff 0%,#fafaff 48%,#f5f7fc 100%);
  color:var(--gb-text);
}
.stApp:before { content:''; position:fixed; inset:0; pointer-events:none; opacity:.35;
  background-image:linear-gradient(rgba(112,87,232,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(112,87,232,.025) 1px,transparent 1px);
  background-size:44px 44px; mask-image:linear-gradient(to bottom,black,transparent 65%); }
[data-testid="stHeader"] { background:rgba(255,255,255,.66); backdrop-filter:blur(22px) saturate(150%); border-bottom:1px solid rgba(55,50,100,.05); }
[data-testid="stToolbar"] { color:#323247; }
.block-container { max-width:1460px; padding-top:4rem; }
.gb-hero { min-height:480px; display:grid; grid-template-columns:minmax(0,1.28fr) minmax(330px,.72fr); gap:clamp(28px,4vw,64px);
  align-items:center; text-align:left; padding:44px 2.2vw 82px; }
.gb-hero-copy { position:relative; z-index:2; }
.gb-hero .gb-companion { order:2; width:clamp(320px,27vw,390px); height:clamp(320px,27vw,390px); justify-self:center; perspective:1000px; }
.gb-companion:before { inset:8% -16% -12%; background:radial-gradient(ellipse at center,rgba(112,87,232,.29),rgba(89,164,239,.12) 42%,transparent 72%); filter:blur(18px); }
.gb-companion:after { width:230px; height:32px; bottom:18px; background:rgba(82,71,190,.18); filter:blur(25px); }
.gb-hero img { width:286px; height:286px; border-radius:66px; transform:rotate(3deg); box-shadow:0 42px 90px rgba(70,58,168,.20),0 8px 28px rgba(81,66,184,.14); filter:none; }
.gb-companion:hover img { transform:translateY(-6px) rotate(1deg) scale(1.018); filter:none; }
.gb-eyebrow { display:inline-flex; align-items:center; gap:8px; padding:8px 13px; border-radius:999px; color:#5d4ccd;
  background:rgba(112,87,232,.08); border:1px solid rgba(112,87,232,.13); letter-spacing:.13em; }
.gb-eyebrow:before { content:''; width:7px; height:7px; border-radius:50%; background:linear-gradient(135deg,#8b68f5,#54b8df); box-shadow:0 0 0 5px rgba(112,87,232,.08); }
.gb-title { max-width:920px; margin:22px 0 20px; color:#17172a; font-size:clamp(3rem,4vw,4.45rem); line-height:1.06; letter-spacing:-.058em; font-weight:720; }
.gb-title-line { display:block; white-space:nowrap; }
.gb-title-line + .gb-title-line { margin-top:.08em; }
.gb-title-gradient { background:linear-gradient(105deg,#9760ec 0%,#648eea 46%,#51bd9d 100%); -webkit-background-clip:text; background-clip:text; color:transparent; }
.gb-subtitle { max-width:650px; color:#63677b; font-size:clamp(1.08rem,1.7vw,1.32rem); line-height:1.65; }
.gb-hero-notes { display:flex; gap:9px; flex-wrap:wrap; margin-top:26px; }
.gb-hero-note { padding:8px 12px; border-radius:999px; color:#666a7c; background:rgba(255,255,255,.7); border:1px solid rgba(54,50,98,.09); box-shadow:0 8px 24px rgba(44,39,94,.05); font-size:.77rem; }
.gb-section { margin:3.6rem 0 1.2rem; }
.gb-section-kicker { color:#735ee0; }
.gb-section h2 { color:#18182b; font-weight:720; }
.gb-section p { color:#777a8c; }
.gb-app-grid { gap:14px; }
.gb-app-card { min-height:190px; background:rgba(255,255,255,.72); border:1px solid rgba(66,59,116,.09);
  box-shadow:0 16px 42px rgba(50,43,105,.07); backdrop-filter:blur(24px) saturate(140%); transition:transform .25s ease,box-shadow .25s ease,border-color .25s ease; }
.gb-app-card:hover { transform:translateY(-4px); border-color:rgba(112,87,232,.20); box-shadow:0 24px 55px rgba(50,43,105,.12); }
.gb-app-card:after { opacity:.18; }
.gb-app-icon { box-shadow:0 11px 25px var(--app-shadow),0 0 0 1px rgba(41,38,72,.06); }
.gb-app-name { color:#1d1d2e; }
.gb-app-route { color:#858797; }
.gb-app-copy { color:#5f6273; }
.gb-app-proof { color:#279769; border-top-color:rgba(42,38,82,.07); }
.gb-app-proof:before { color:white; }
.gb-status { color:#24885e; background:rgba(45,187,121,.08); border-color:rgba(45,187,121,.16); }
[data-testid="stForm"], [data-testid="stMetric"], .gb-card { background:rgba(255,255,255,.78); border-color:rgba(60,54,112,.10); box-shadow:0 22px 60px rgba(48,41,100,.08); backdrop-filter:blur(24px); }
[data-testid="stForm"] { padding:1.15rem 1.3rem 1.25rem; border-radius:28px; background:linear-gradient(145deg,rgba(255,255,255,.91),rgba(249,248,255,.82)); border-color:rgba(119,91,220,.11); box-shadow:0 24px 64px rgba(73,58,145,.09),inset 0 1px 0 rgba(255,255,255,.9); }
[data-testid="stTextInput"] label p { color:#625b80 !important; font-size:.76rem; font-weight:680; letter-spacing:.04em; }
[data-testid="stTextInputRootElement"],
[data-testid="stTextInput"] div[data-baseweb="input"] { background:linear-gradient(120deg,#fff 0%,#faf8ff 100%) !important; border:1px solid rgba(120,91,224,.19) !important; border-radius:13px !important; box-shadow:inset 0 1px 0 #fff,0 8px 24px rgba(83,65,158,.05) !important; transition:border-color .2s ease,box-shadow .2s ease; color-scheme:light; }
[data-testid="stTextInputRootElement"]:hover,
[data-testid="stTextInput"] div[data-baseweb="input"]:hover { border-color:rgba(120,91,224,.30) !important; }
[data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within { background:#fff !important; border-color:#8068e9 !important; box-shadow:0 0 0 4px rgba(112,87,232,.10),0 12px 30px rgba(80,62,164,.08) !important; }
[data-testid="stTextInput"] input { background:transparent !important; border:0 !important; color:#19172c; min-height:56px; padding-inline:18px; font-size:1rem; box-shadow:none !important; outline:0 !important; }
[data-testid="stTextInput"] input:hover { border:0 !important; }
[data-testid="stTextInput"] input::placeholder { color:#8d87a5; opacity:1; font-weight:450; }
[data-testid="stTextInput"] input:focus { box-shadow:none !important; }
[data-testid="stFormSubmitButton"] button { min-height:58px; white-space:nowrap; padding-inline:22px; }
[data-testid="stFormSubmitButton"] button p { white-space:nowrap; font-size:.92rem; font-weight:700; }
[data-testid="stFormSubmitButton"] button, .stButton > button[kind="primary"] { background:linear-gradient(135deg,#8a6df1 0%,#6d59df 52%,#5f70dc 100%); box-shadow:0 14px 30px rgba(100,78,210,.25),inset 0 1px 0 rgba(255,255,255,.22); }
[data-testid="stFormSubmitButton"] button:hover { background:linear-gradient(135deg,#9278f3,#7562e4 52%,#6879e2); box-shadow:0 17px 36px rgba(100,78,210,.30); }
.stButton > button { border-radius:14px !important; transition:transform .18s ease,box-shadow .18s ease,background .18s ease !important; }
.stButton > button:focus, .stButton > button:focus-visible,
[data-testid="stFormSubmitButton"] button:focus,
[data-testid="stFormSubmitButton"] button:focus-visible { outline:0 !important; border-color:transparent !important; box-shadow:0 0 0 4px rgba(0,122,255,.14),0 14px 30px rgba(76,91,190,.18) !important; }
.gb-notice { display:flex; align-items:flex-start; gap:13px; margin:14px 2px; padding:14px 16px; border-radius:18px;
  background:rgba(255,255,255,.74); border:1px solid rgba(60,60,67,.10); box-shadow:0 12px 34px rgba(35,36,58,.06); backdrop-filter:blur(24px) saturate(165%); }
.gb-notice-icon { width:26px; height:26px; flex:0 0 auto; display:grid; place-items:center; border-radius:50%; margin-top:1px;
  color:#fff; background:#8e8e93; font-size:.76rem; font-weight:800; box-shadow:inset 0 1px 0 rgba(255,255,255,.28); }
.gb-notice-copy { min-width:0; padding-top:2px; }
.gb-notice-title { color:#1d1d1f; font-size:.92rem; line-height:1.42; font-weight:650; letter-spacing:-.01em; }
.gb-notice-detail { margin-top:3px; color:#6e6e73; font-size:.78rem; line-height:1.5; }
.gb-notice.success { border-color:rgba(52,199,89,.16); background:linear-gradient(135deg,rgba(244,255,248,.88),rgba(255,255,255,.76)); }
.gb-notice.success .gb-notice-icon { background:#34c759; }
.gb-notice.error { border-color:rgba(255,59,48,.14); background:linear-gradient(135deg,rgba(255,247,246,.92),rgba(255,255,255,.76)); }
.gb-notice.error .gb-notice-icon { background:#ff453a; }
.gb-notice.info { border-color:rgba(0,122,255,.14); background:linear-gradient(135deg,rgba(245,250,255,.92),rgba(255,255,255,.76)); }
.gb-notice.info .gb-notice-icon { background:#0a84ff; font-family:Georgia,serif; }
.gb-notice.warning { border-color:rgba(255,159,10,.16); background:linear-gradient(135deg,rgba(255,250,241,.92),rgba(255,255,255,.76)); }
.gb-notice.warning .gb-notice-icon { background:#ff9f0a; }
[data-testid="stAlert"] { padding:13px 15px !important; border-radius:18px !important; background:rgba(255,255,255,.76) !important; border:1px solid rgba(60,60,67,.10) !important; box-shadow:0 12px 34px rgba(35,36,58,.055) !important; backdrop-filter:blur(24px) saturate(165%); }
[data-testid="stAlert"] p { font-size:.88rem !important; line-height:1.45 !important; }
.gb-request-strip { display:flex; align-items:center; justify-content:space-between; gap:18px; margin:16px 2px 18px; padding:14px 16px; border-radius:20px; background:rgba(255,255,255,.72); border:1px solid rgba(60,60,67,.10); box-shadow:0 14px 38px rgba(35,36,58,.06); backdrop-filter:blur(24px) saturate(165%); }
.gb-request-main { min-width:0; display:flex; align-items:center; gap:11px; }
.gb-request-icon { width:36px; height:36px; flex:0 0 auto; display:grid; place-items:center; border-radius:12px; color:#fff; background:linear-gradient(145deg,#7c68ee,#5c7fe8); box-shadow:0 8px 18px rgba(100,88,220,.20); font-size:.88rem; }
.gb-request-copy { min-width:0; }
.gb-request-title { color:#242438; font-size:.84rem; font-weight:690; }
.gb-request-meta { margin-top:2px; color:#9395a5; font-size:.67rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.gb-request-state { flex:0 0 auto; display:inline-flex; align-items:center; gap:7px; padding:7px 10px; border-radius:999px; font-size:.68rem; font-weight:760; letter-spacing:.06em; }
.gb-request-state:before { content:''; width:6px; height:6px; border-radius:50%; background:currentColor; box-shadow:0 0 0 4px color-mix(in srgb,currentColor 12%,transparent); }
.gb-request-state.completed { color:#23885d; background:rgba(45,187,121,.09); border:1px solid rgba(45,187,121,.15); }
.gb-request-state.running,.gb-request-state.queued { color:#6651d8; background:rgba(112,87,232,.08); border:1px solid rgba(112,87,232,.14); }
.gb-request-state.failed { color:#b55a58; background:rgba(213,91,86,.08); border:1px solid rgba(213,91,86,.14); }
[data-testid="stExpander"] { background:rgba(255,255,255,.58); border-color:rgba(60,54,112,.09); }
.gb-contract { background:rgba(255,255,255,.78); border-color:rgba(112,87,232,.14); box-shadow:0 22px 55px rgba(53,45,111,.08); }
.gb-contract-title { color:#1b1b2c; }
.gb-contract-block { background:rgba(246,246,251,.82); border-color:rgba(57,52,102,.07); }
.gb-contract-block div { color:#56596b; }
.gb-analysis { margin:0 0 14px; padding:15px 16px; border-radius:15px; background:linear-gradient(135deg,rgba(118,87,232,.08),rgba(74,173,226,.07)); border:1px solid rgba(112,87,232,.13); }
.gb-analysis-head { margin-bottom:10px; color:#5f4ed0; font-size:.72rem; font-weight:800; letter-spacing:.09em; }
.gb-analysis-grid { display:grid; grid-template-columns:1fr 1.35fr; gap:10px 22px; }
.gb-analysis-item b { display:block; color:#848697; font-size:.64rem; letter-spacing:.1em; margin-bottom:4px; }
.gb-analysis-item div { color:#343648; font-size:.76rem; line-height:1.55; }
.gb-screen-placeholder { background:rgba(255,255,255,.58); }
.gb-phone { background:linear-gradient(145deg,#f4f3fa,#cbc9d8 48%,#aaa8b8); border-color:rgba(77,70,120,.14); box-shadow:0 35px 90px rgba(53,47,111,.16),inset 0 1px 0 rgba(255,255,255,.9); }
.gb-phone-screen { background:radial-gradient(circle at 50% 36%,rgba(129,101,234,.14),transparent 35%),linear-gradient(160deg,#ffffff 0%,#f8f7fd 56%,#f0eef9 100%); border-color:rgba(78,70,124,.09); }
.gb-receipt { background:rgba(255,255,255,.82); box-shadow:0 20px 55px rgba(42,38,91,.08); }
.gb-receipt-title { color:#1b1b2b; }
.gb-proof-answer { background:#f7f7fb; border-color:rgba(54,49,96,.08); }
@media (max-width:980px) {
  .gb-hero { grid-template-columns:1fr; min-height:auto; text-align:center; gap:18px; padding-top:12px; }
  .gb-hero-copy { order:2; }
  .gb-hero .gb-companion { order:1; width:250px; height:230px; }
  .gb-hero img { width:180px; height:180px; border-radius:42px; }
  .gb-title { max-width:760px; margin-inline:auto; font-size:clamp(2.8rem,8vw,4.7rem); }
  .gb-title-line { white-space:normal; }
  .gb-subtitle { margin-inline:auto; }
  .gb-hero-notes { justify-content:center; }
}
@media (max-width:720px) {
  .gb-request-strip { align-items:flex-start; }
  .gb-request-state { margin-top:1px; }
  [data-testid="stFormSubmitButton"] button { width:100%; }
}
</style>
""", unsafe_allow_html=True)

icon_data = ""
if ICON.exists():
    icon_data = base64.b64encode(ICON.read_bytes()).decode("ascii")
st.markdown(f"""
<div class="gb-hero">
  <div class="gb-companion"><img src="data:image/svg+xml;base64,{icon_data}" alt="BearBless bear icon" /></div>
  <div class="gb-hero-copy">
    <div class="gb-eyebrow">Non-interruptive mobile agent</div>
    <div class="gb-title"><span class="gb-title-line">陪伴，不止陪伴</span><span class="gb-title-line gb-title-gradient">琐事，交给 BearBless</span></div>
    <p class="gb-subtitle">你继续拥有手机主屏，小熊在不可见的平行空间完成任务。需要你决定时才出现，完成后留下可验证的结果。</p>
    <div class="gb-hero-notes"><span class="gb-hero-note">主屏零打扰</span><span class="gb-hero-note">边界先确认</span><span class="gb-hero-note">结果可验证</span></div>
  </div>
</div>
""", unsafe_allow_html=True)

st.markdown("<div class='gb-section'><div class='gb-section-kicker'>语音或文字</div><h2>告诉小熊你想做什么</h2><p>点击麦克风即可实时看到简体中文听写；停止后自动填入任务框，不会直接触发手机操作。</p></div>", unsafe_allow_html=True)
app_modules = (
    ("Wolt", image_data_uri(APP_ICONS_ROOT / "wolt.jpg"), "#10c3a5", "rgba(16,195,165,.28)", "免搜索框分类浏览", PRODUCT_CAPABILITIES[0][1], "店名 · 评分 · 营业状态 · 地址"),
    ("QQ", image_data_uri(APP_ICONS_ROOT / "qq.jpg"), "#168cff", "rgba(22,140,255,.3)", "消息草稿安全编辑", "在指定联系人会话中写入草稿，不点击发送", "联系人 · 原文草稿 · 零发送"),
    ("网易云音乐", image_data_uri(APP_ICONS_ROOT / "netease.jpg"), "#e63b32", "rgba(230,59,50,.3)", "精确歌曲深链", PRODUCT_CAPABILITIES[3][1], "MediaSession 标题与播放态"),
    ("YouTube", image_data_uri(APP_ICONS_ROOT / "youtube.svg"), "#ff0033", "rgba(255,0,51,.28)", "免输入法搜索深链", PRODUCT_CAPABILITIES[4][1], "搜索词 · 结果页 · 可见视频"),
    ("系统时钟", image_data_uri(APP_ICONS_ROOT / "huawei-clock.png"), "#f29f3d", "rgba(242,159,61,.28)", "虚拟屏滚轮设置", PRODUCT_CAPABILITIES[1][1], "目标时间 · 已保存 · 已开启"),
)
capability_html = "".join(
    f'<div class="gb-app-card" style="--app-color:{color};--app-glow:{shadow};--app-shadow:{shadow}">'
    f'<div class="gb-app-head"><div class="gb-app-icon"><img src="{icon}" alt="{safe_text(name)}应用图标"></div>'
    f'<div><div class="gb-app-name">{safe_text(name)}</div><div class="gb-app-route">{safe_text(route)}</div></div></div>'
    f'<div class="gb-app-copy">{safe_text(detail)}</div><div class="gb-app-proof">{safe_text(proof)}</div></div>'
    for name, icon, color, shadow, route, detail, proof in app_modules
)
st.markdown(
    f'<div class="gb-app-grid">{capability_html}</div>'
    '<div class="gb-status"><span class="gb-dot"></span>当前范围：Wolt · 闹钟 · QQ · 网易云音乐 · YouTube；餐厅地址直接来自 Wolt 店铺详情</div>',
    unsafe_allow_html=True,
)
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
    input_col, submit_col = st.columns([5, 1.35], vertical_alignment="bottom")
    with input_col:
        goal = st.text_input(
            "任务文字",
            key="task_goal",
            placeholder="例如：在 Wolt 找一家评分高的中餐店，并读取店名、营业状态和地址",
        )
    with submit_col:
        reviewed = st.form_submit_button("生成任务", type="primary", width="stretch")
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
    analysis = build_local_task_analysis(str(contract_payload["goal"]))
    analysis_steps = " → ".join(str(item) for item in analysis["steps"])
    allowed = "".join(f"<div>✓ {safe_text(item)}</div>" for item in contract_payload.get("allowed_actions", []))
    forbidden = "".join(f"<div>× {safe_text(item)}</div>" for item in contract_payload.get("forbidden_actions", []))
    criteria = "".join(f"<div>○ {safe_text(item['description'])}</div>" for item in contract_payload.get("success_criteria", []))
    st.markdown(
        f'<div class="gb-contract"><div class="gb-contract-head"><div class="gb-contract-title">Mission Contract · {safe_text(contract_payload["goal"])}</div>'
        f'<div class="gb-contract-chip">INTERRUPTION BUDGET · {safe_text(contract_payload.get("interruption_budget", 0))}</div></div>'
        f'<div class="gb-analysis"><div class="gb-analysis-head">✦ AGENT 本地分析</div><div class="gb-analysis-grid">'
        f'<div class="gb-analysis-item"><b>任务理解</b><div>{safe_text(analysis["intent"])}</div></div>'
        f'<div class="gb-analysis-item"><b>执行路线</b><div>{safe_text(analysis["route"])}</div></div>'
        f'<div class="gb-analysis-item"><b>操作计划</b><div>{safe_text(analysis_steps)}</div></div>'
        f'<div class="gb-analysis-item"><b>护栏与证据</b><div>{safe_text(analysis["guard"])}<br>{safe_text(analysis["proof"])}</div></div>'
        f'</div></div>'
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
        st.session_state["hide_history_after_terminal"] = False
        st.session_state.pop("cleared_terminal_key", None)
        st.session_state.pop("cleared_request_key", None)
        render_notice(
            "success",
            "任务已交给 BearBless",
            f"{request_path.stem} · 可以继续使用手机，无需停留在此页面",
        )
        del st.session_state["mission_contract"]

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
    if "system keyboard" in reason or "Display 0" in reason and "IME" in reason:
        return "检测到 Agent 触发了手机主屏输入法，已立即停止任务以保护用户操作。"
    return reason


def idle_phone_html(title: str, detail: str) -> str:
    return (
        '<div class="gb-phone"><div class="gb-phone-screen"><div class="gb-phone-idle">'
        f'<div class="gb-phone-logo"><img src="data:image/svg+xml;base64,{icon_data}" alt="BearBless Logo"></div>'
        f'<b>{safe_text(title)}</b><small>{safe_text(detail)}</small>'
        '</div></div></div>'
    )


def render_shadow_startup(
    message: str,
    *,
    title: str = "正在建立隔离虚拟屏",
    detail: str = "首帧生成后会自动显示在这里",
) -> None:
    """Keep the virtual-phone stage visible before the first run frame exists."""
    render_notice("info", message)
    _, phone_col, _ = st.columns([.78, 1.34, .88], gap="large")
    with phone_col:
        st.markdown('<div class="gb-panel-title" style="text-align:center">Agent 虚拟手机</div>', unsafe_allow_html=True)
        st.markdown(idle_phone_html(title, detail), unsafe_allow_html=True)


@st.fragment(run_every=LIVE_REFRESH_SECONDS)
def render_live_workbench() -> None:
    newest_requests = sorted(
        REQUESTS_ROOT.glob("request-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if REQUESTS_ROOT.exists() else []
    newest_request = read_json(newest_requests[0]) if newest_requests else {}
    if newest_request:
        request_status = str(newest_request.get("status") or "QUEUED").upper()
        status_label = {
            "COMPLETED": "已完成",
            "RUNNING": "执行中",
            "QUEUED": "排队中",
            "FAILED": "已停止",
        }.get(request_status, request_status)
        request_meta = f"{newest_request.get('request_id') or '—'}"
        if newest_request.get("task_id"):
            request_meta += f" · {newest_request.get('task_id')}"
        st.markdown(
            '<div class="gb-request-strip">'
            '<div class="gb-request-main"><div class="gb-request-icon">✦</div><div class="gb-request-copy">'
            f'<div class="gb-request-title">最近任务</div><div class="gb-request-meta">{safe_text(request_meta)}</div>'
            '</div></div>'
            f'<div class="gb-request-state {safe_text(request_status.lower())}">{safe_text(status_label)}</div>'
            '</div>',
            unsafe_allow_html=True,
        )
    if newest_request.get("status") == "FAILED" and not newest_request.get("task_id"):
        request_key = str(newest_request.get("request_id") or "unknown-request")
        if st.session_state.get("cleared_request_key") == request_key:
            return
        raw_error = str(newest_request.get("error") or "启动前检查失败")
        if "QQ is currently foreground on Display 0" in raw_error:
            raw_error = "主屏正在使用 QQ，为避免打扰，任务没有启动"
        render_notice("error", "任务未启动", raw_error)
        render_shadow_startup(
            "任务在虚拟屏创建前停止，因此本次没有可展示的屏幕帧。",
            title="虚拟屏未创建",
            detail="请查看上方失败原因；修复后重新提交任务即可恢复实时画面",
        )
        if st.button("清除失败记录并返回待命页", key=f"dismiss-request:{request_key}", width="stretch"):
            st.session_state["cleared_request_key"] = request_key
            st.session_state["hide_history_after_terminal"] = True
            st.session_state["reset_after_terminal"] = True
            st.rerun()
        return
    if newest_request.get("status") == "QUEUED":
        render_shadow_startup("任务已排队，等待 Worker 接管；虚拟屏尚未创建。")
        return
    live_run = latest_run()
    if live_run is None:
        if newest_request.get("status") == "RUNNING":
            render_shadow_startup("Worker 已接管任务，正在完成设备检查并创建隔离虚拟屏。")
        return
    live_state = read_json(live_run / "state.json")
    if (
        newest_request.get("status") == "RUNNING"
        and newest_request.get("goal")
        and live_state.get("goal") != newest_request.get("goal")
    ):
        render_shadow_startup("Worker 已接管任务，正在完成设备检查并创建隔离虚拟屏。")
        return
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
    if status == "REPLANNING" and failure_reason:
        current_reason = failure_reason
    takeover = live_state.get("collected_data", {}).get("human_takeover")
    if isinstance(takeover, dict) and takeover.get("kind") == "verification":
        st.warning(
            "需要人工验证：请在电脑上的 BearBless Shadow Display 窗口手动完成滑块；完成后 Agent 会自动继续。",
            icon="🖐️",
        )
    terminal_notice_key = f"{live_run.name}:{status}"
    if status in {"COMPLETED", "FAILED"} and st.session_state.get("cleared_terminal_key") == terminal_notice_key:
        return
    if status == "COMPLETED":
        summary = str(live_state.get("collected_data", {}).get("agent_result") or "任务已完成并通过验证")
        timer_key = f"terminal_started:{terminal_notice_key}"
        if timer_key not in st.session_state:
            st.session_state[timer_key] = time.monotonic()
        elapsed = time.monotonic() - float(st.session_state[timer_key])
        st.success(f"任务完成：{summary}", icon="✅")
        remaining = max(0, 5 - int(elapsed))
        st.caption(f"{remaining} 秒后自动清空任务并刷新回待命页；历史证据仍会保留。")
        if st.session_state.get("terminal_notice") != terminal_notice_key:
            st.toast(f"BearBless 已完成：{summary}", icon="✅")
            st.session_state["terminal_notice"] = terminal_notice_key
        if elapsed >= 5:
            st.session_state["cleared_terminal_key"] = terminal_notice_key
            st.session_state["hide_history_after_terminal"] = True
            st.session_state["reset_after_terminal"] = True
            st.rerun()
        if st.button("立即结束并清空", key=f"dismiss:{terminal_notice_key}", width="stretch"):
            st.session_state["cleared_terminal_key"] = terminal_notice_key
            st.session_state["hide_history_after_terminal"] = True
            st.session_state["reset_after_terminal"] = True
            st.rerun()
    elif status == "FAILED":
        readable_failure = human_failure_reason(live_state)
        st.error(f"任务未完成：{readable_failure}", icon="⚠️")
        st.caption("失败原因和最后现场将保留，直到你手动清除或提交新任务。")
        current_reason = "任务已停止，不会继续执行"
        if st.session_state.get("terminal_notice") != terminal_notice_key:
            st.toast(f"任务已停止：{readable_failure}", icon="⚠️")
            st.session_state["terminal_notice"] = terminal_notice_key
        if st.button("清除失败记录并返回待命页", key=f"dismiss:{terminal_notice_key}", width="stretch"):
            st.session_state["cleared_terminal_key"] = terminal_notice_key
            st.session_state["hide_history_after_terminal"] = True
            st.session_state["reset_after_terminal"] = True
            st.rerun()
    else:
        st.info(f"任务执行中 · 第 {step_index} 步 · {current_reason}", icon="🐻")
        request_id = str(newest_request.get("request_id") or "")
        if st.button("结束当前任务", key=f"cancel:{request_id or live_run.name}", type="secondary", width="stretch"):
            request_path = newest_requests[0] if newest_requests else None
            if request_path is not None and request_cancellation(request_path):
                st.warning("已请求结束任务；Agent 将在当前安全步骤结束后停止并清理虚拟屏。", icon="⏹️")
            else:
                st.info("任务已经结束或尚未被 Worker 接管。", icon="⏹️")
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
        if status not in {"COMPLETED", "FAILED"}:
            st.caption("低延迟实时画面：BearBless Shadow Display 原生窗口 · 此处为隔离证据帧")
        if live_frames:
            # While a task is active, always render the newest frame directly.
            # A persistent slider keeps its previous selection when new options
            # arrive, which made the web projection appear one frame behind.
            selected_frame = len(live_frames) - 1
            if status in {"COMPLETED", "FAILED"} and len(live_frames) > 1:
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
                    key=f"frame-replay:{live_run.name}",
                )
            selected_path = live_frames[selected_frame]
            # Supplying bytes prevents the frontend/media cache from serving an
            # older image when the latest screenshot is replaced on disk.
            st.image(
                selected_path.read_bytes(),
                caption=f"Shadow display · {selected_path.name}",
                width="stretch",
            )
        else:
            st.markdown(idle_phone_html("等待 Agent 虚拟屏", "连接手机后将在这里实时出现"), unsafe_allow_html=True)

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
            f'<div class="gb-action-meta">{safe_text(failure_reason if status == "REPLANNING" and failure_reason else live_state.get("current_subgoal") or "Runtime 正在等待新的任务状态")}</div></div>',
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
if run is None or st.session_state.get("hide_history_after_terminal") is True:
    st.markdown(idle_phone_html("BearBless 已待命", "输入下一项任务即可开始"), unsafe_allow_html=True)
    if run is not None:
        st.stop()
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
