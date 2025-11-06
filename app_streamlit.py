# app_streamlit.py
import os, sys, json, random, time, traceback
from typing import Dict, List, Optional

# --- 안전한 모듈 경로 보장 ---
if "/workspace" not in sys.path:
    sys.path.append("/workspace")

import streamlit as st
import streamlit.components.v1 as components

# ============== 내부 로직 임포트 ==============
try:
    from mafia_core import (
        GameState, Phase,
        create_default_game, mafia_kill,
        tally_votes_plurality, check_win
    )
except Exception:
    st.set_page_config(page_title="마피아 (Ollama EEVE)", page_icon="🕵️", layout="wide")
    st.error("🚫 `mafia_core` 임포트 실패")
    st.code(traceback.format_exc())
    st.stop()

try:
    # llm_engine.llm_step(gs, player_input, goal, memory_snapshot=None) 형태 가정
    from llm_engine import llm_step as _llm_step_raw
except Exception:
    _llm_step_raw = None

# ====== 환경변수 ======
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "EEVE-Korean-10.8B")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))

# ====== LLM 지연 로딩(첫 화면 즉시 표시) ======
@st.cache_resource
def get_llm_step():
    """
    무거운 초기화를 내부에서 처리하도록 래핑.
    llm_engine 내부가 무거울 수 있으므로 캐시.
    """
    if _llm_step_raw is None:
        return None
    return _llm_step_raw

# =========================
# 페이지 설정 & 다크 스타일
# =========================
st.set_page_config(page_title="마피아 (Ollama EEVE)", page_icon="🕵️", layout="wide")

DARK_CSS = """
<style>
:root, .stApp, .main { background: #0d0f13 !important; color: #e6e6e6 !important; }
.block-container { max-width: 900px !important; }
.chat-wrap { border: 1px solid #222831; border-radius: 14px; background: #11131a; padding: 14px 16px; min-height: 360px; }
.chat-bubble { background: #1a1f2b; border: 1px solid #22293a; border-radius: 12px; padding: 10px 12px; margin: 6px 0; }
.chat-bubble.me { background: #243049; border-color:#2e3c5a; }
.role-pill { font-size: 12px; padding: 2px 8px; border-radius: 999px; background: #1b2230; border:1px solid #2b3447; color:#b7c2d8; }
.divider { height: 1px; background:#222831; margin: 12px 0; }
.btn-row { display: flex; gap: 8px; flex-wrap: wrap; }
.vote-box { background:#121621; border:1px solid #232a3a; border-radius:14px; padding:12px; }
.toast { color:#9fb3ff; font-size:12px; opacity:.85; }
.audio-hint { font-size:12px; color:#88a; }
</style>
"""
st.markdown(DARK_CSS, unsafe_allow_html=True)

# ============= BGM (auto) =============
if "bgm_unmuted" not in st.session_state:
    st.session_state.bgm_unmuted = False

def bgm_html(unmuted: bool) -> str:
    # static/bgm.mp3 가 있으면 재생 (없어도 에러는 안 남)
    base = st.get_option("server.baseUrlPath") or ""
    src  = f"{base}/static/bgm.mp3"
    muted_attr = "" if unmuted else "muted"
    return f"""
    <audio id="bgm" autoplay loop {muted_attr} style="display:none">
      <source src="{src}" type="audio/mpeg">
    </audio>
    <script>
      const a = document.getElementById('bgm');
      if (a && !a.muted) {{ a.play().catch(()=>{{}}); }}
    </script>
    """

st.markdown(bgm_html(st.session_state.bgm_unmuted), unsafe_allow_html=True)
colA, colB = st.columns([1, 6])
with colA:
    if st.button("🔊 Unmute" if not st.session_state.bgm_unmuted else "🔇 Mute",
                 use_container_width=True, key="btn_bgm_toggle"):
        st.session_state.bgm_unmuted = not st.session_state.bgm_unmuted
with colB:
    st.markdown('<div class="audio-hint">브라우저 정책으로 자동재생이 막히면 Unmute를 한번 눌러 주세요.</div>', unsafe_allow_html=True)

# ===================== 세션 상태 =====================
def _init_game():
    gs = create_default_game()
    if not hasattr(gs, "dialogue_history"):
        gs.dialogue_history = []
    return {"gs": gs, "phase": "NIGHT", "first_night_done": False, "day_start_idx": 0}

if "state" not in st.session_state:
    st.session_state.state = _init_game()

S = st.session_state.state
gs: GameState = S["gs"]

# ===================== 유틸 =====================
FALLBACKS = [
    "(주위를 살핀다.)", "(눈을 피한다.)", "(작게 한숨을 쉰다.)",
    "(입술을 깨문다.)", "(아무 말 없이 분위기를 살핀다.)",
    "(잠시 침묵이 흐른다...)", "(의심스러운 표정으로 주변을 바라본다.)",
]
GENERIC = {
    "저도 마찬가지입니다.", "동의합니다.", "단결해서 이 위기를 헤쳐나가자구요.",
    "항상 경계심을 가져야 합니다.", "조심해야 해요.", "믿고 단합합시다."
}

def anti_repeat(name: str, line: str, last_by_name: Dict[str, str]) -> str:
    norm = (line or "").strip()
    if (not norm) or (norm in GENERIC) or (norm == last_by_name.get(name, "")):
        return random.choice(FALLBACKS)
    return norm

def append_dialog(name: str, text: str):
    if not hasattr(gs, "dialogue_history"):
        gs.dialogue_history = []
    gs.dialogue_history.append(f"{name}: {text}")

def alive_ai(gs: GameState) -> List[str]:
    return [n for n in gs.alive_players() if n != "당신"]

def vote_targets(gs: GameState) -> List[str]:
    return [n for n in gs.alive_players() if n != "당신"]

def render_chat_box(lines, height=420, width_px=720):
    css = f"""
    <style>
      .chat-wrap {{ width: 100%; display: flex; justify-content: center; align-items: flex-start; }}
      .chat-card {{ width: {width_px}px; max-width: 92vw; height: {height}px;
        background: #0f1116; border: 1px solid #2a2f3a; border-radius: 16px;
        padding: 14px; overflow: auto; box-shadow: 0 4px 20px rgba(0,0,0,.35); }}
      .msg {{ display: grid; grid-template-columns: 84px 1fr; gap: 8px; margin: 8px 0; }}
      .name {{ color: #a0aec0; font-weight: 600; font-size: 12px; text-align:right; padding-top: 6px; }}
      .bubble {{ background: #161a24; color: #e6edf3; border: 1px solid #2a2f3a; border-radius: 12px; padding: 10px 12px; line-height: 1.35; }}
      .narrator .name {{ color:#9ae6b4; }}
      .you .bubble {{ background:#1e2433; border-color:#334155; }}
    </style>
    """
    body = []
    for name, text in lines:
        cls = "narrator" if name in ("사회자","내레이터") else ("you" if name=="당신" else "")
        body.append(f'''
          <div class="msg {cls}">
            <div class="name">{name}</div>
            <div class="bubble">{text}</div>
          </div>
        ''')
    html = f"""{css}
    <div class="chat-wrap"><div id="chat-box" class="chat-card">{''.join(body)}</div></div>
    <script>const box = document.getElementById('chat-box'); if (box) {{ box.scrollTop = box.scrollHeight; }}</script>
    """
    components.html(html, height=height+36, scrolling=False)

# ========================= Sidebar =========================
with st.sidebar:
    st.markdown("### 🕵️ 마피아 (Ollama EEVE)")
    st.markdown(f'<span class="role-pill">Round {gs.round}</span>', unsafe_allow_html=True)
    st.write(f"**Role:** {gs.players['당신'].role}")
    st.write("**Alive:** " + ", ".join(gs.alive_players()))
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    if c1.button("🔁 새 판 시작", use_container_width=True):
        st.session_state.state = _init_game()
        st.rerun()
    if c2.button("🧹 로그 지우기", use_container_width=True):
        gs.dialogue_history.clear()
        st.rerun()
    # 상태 표시(디버그 도움)
    st.caption(f"OLLAMA_URL: {OLLAMA_URL}")
    st.caption(f"MODEL_NAME: {MODEL_NAME}")

# ========================= 헤더 =========================
st.markdown("## 🕵️ 마피아 게임 (Streamlit / Chat 스타일)")
st.caption("Dark UI / 중앙 채팅 / 턴 진행 / 투표 버튼 / BGM")

# ========================= 라운드 배너 =========================
st.markdown(
    f"**Round {gs.round}** &nbsp;|&nbsp; **Role:** {gs.players['당신'].role} &nbsp;|&nbsp; "
    f"**Alive:** {', '.join(gs.alive_players())}"
)

# ========================= 채팅 출력 =========================
msgs = []
for line in gs.dialogue_history[-120:]:
    if ": " in line:
        name, text = line.split(": ", 1)
    else:
        name, text = "사회자", line
    msgs.append((name, text))
render_chat_box(msgs, height=440, width_px=760)

# ========================= 턴 로직 =========================
def do_night():
    if not S["first_night_done"]:
        append_dialog("사회자", "어둠이 내려앉고, 모두가 서로의 기척을 탐색합니다. (첫 밤은 조용히 지나갑니다)")
        S["first_night_done"] = True
        S["phase"] = "DAY_DISCUSS"
        return

    killed = mafia_kill(gs)
    if killed:
        if killed == "당신":
            append_dialog("사회자", f"💥 밤에 {killed}이(가) 마피아에게 사망했습니다. 마피아 팀 승리!")
            st.error("마피아 팀 승리! 게임 종료.")
            st.stop()
        else:
            append_dialog("사회자", f"💥 밤에 {killed}이(가) 마피아에게 사망했습니다.")
    else:
        append_dialog("사회자", "…아무 일도 일어나지 않았습니다.")

    w = check_win(gs)
    if w:
        append_dialog("사회자", "🎉 시민 팀 승리!" if w == "CITIZEN_WIN" else "💀 마피아 팀 승리!")
        st.stop()

    S["phase"] = "DAY_DISCUSS"

def do_discuss(user_text: Optional[str]):
    last_by_name: Dict[str, str] = {}
    if user_text and user_text.strip():
        append_dialog("당신", user_text.strip())

    llm_step = get_llm_step()
    if llm_step is not None:
        try:
            # 필요하면 요약 제공: hasattr(gs, "to_summary_json")
            prompt_out = llm_step(gs, user_text or "(...)", "discuss")
            # 기대 형식: {"character_lines":[{"name":"민수","line":"..."}]}
            lines = []
            for it in (prompt_out or {}).get("character_lines", []):
                name = it.get("name"); line = it.get("line")
                if name in alive_ai(gs) and line:
                    fixed = anti_repeat(name, line, last_by_name)
                    append_dialog(name, fixed)
                    last_by_name[name] = fixed
            # 누락 보강
            spoken = set(last_by_name.keys())
            for n in alive_ai(gs):
                if n not in spoken:
                    append_dialog(n, random.choice(FALLBACKS))
        except Exception:
            # LLM 실패 시 묘사로 채움(에러는 화면에 노출)
            st.warning("⚠️ LLM 응답 실패 — 임시 대사로 대체합니다.")
            st.code(traceback.format_exc())
            for n in alive_ai(gs):
                append_dialog(n, random.choice(FALLBACKS))
    else:
        for n in alive_ai(gs):
            append_dialog(n, random.choice(FALLBACKS))

def do_vote(user_choice: str):
    targets = vote_targets(gs)
    allowed = targets + ["무처형"]
    if user_choice not in allowed:
        user_choice = "무처형"

    votes: Dict[str, str] = {"당신": user_choice}
    pick = targets[0] if targets else "무처형"
    for v in targets:
        votes[v] = pick

    executed, counter = tally_votes_plurality(
        votes, gs.alive_players(), allow_no_lynch=True, no_lynch_label="무처형"
    )

    tally_lines = [f"{k}: {v}표" for k, v in sorted(counter.items(), key=lambda x: (-x[1], x[0]))]
    append_dialog("사회자", "🧮 득표 현황\n- " + "\n- ".join(tally_lines))

    if executed:
        append_dialog("사회자", f"✅ 처형: {executed}")
        gs.players[executed].alive = False
        if hasattr(gs, "log"): gs.log.append(f"낮 투표로 {executed}이(가) 처형되었습니다.")
    else:
        append_dialog("사회자", "❎ 무처형")
        if hasattr(gs, "log"): gs.log.append("낮 투표 무처형.")

    w = check_win(gs)
    if w:
        append_dialog("사회자", "🎉 시민 팀 승리!" if w == "CITIZEN_WIN" else "💀 마피아 팀 승리!")
        st.stop()

    gs.round += 1
    S["phase"] = "NIGHT"

# ========================= 하단 UI =========================
st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

if S["phase"] == "NIGHT":
    st.subheader(f"🌙 밤 {gs.round}")
    col1, col2 = st.columns([1, 3])
    if col1.button("밤 진행", use_container_width=True):
        do_night(); st.rerun()
    col2.button("다음 단계로", use_container_width=True, on_click=lambda: (do_night(), st.rerun()))

elif S["phase"] == "DAY_DISCUSS":
    st.subheader("☀️ 낮 토론")
    user_text = st.chat_input("한 줄 입력 후 Enter")
    if user_text is not None:
        do_discuss(user_text); st.rerun()
    st.markdown('<div class="btn-row">', unsafe_allow_html=True)
    if st.button("🗳 투표 단계로 이동"):
        S["phase"] = "DAY_VOTE"; st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

elif S["phase"] == "DAY_VOTE":
    st.subheader("🗳 투표")
    targets = vote_targets(gs)
    choice = st.radio("대상 선택", options=targets + ["무처형"],
                      horizontal=False, index=0 if targets else len(targets))
    c1, c2 = st.columns([1, 3])
    if c1.button("투표 실행", use_container_width=True):
        do_vote(choice); st.rerun()
    if c2.button("토론으로 돌아가기", use_container_width=True):
        S["phase"] = "DAY_DISCUSS"; st.rerun()
