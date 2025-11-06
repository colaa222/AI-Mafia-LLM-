# app_streamlit.py — Streamlit UI (Dark / Center Chat / BGM / Turn & Vote)
import json, random, re
from typing import Dict, List, Optional
import streamlit as st
import streamlit.components.v1 as components

# === 내부 로직 모듈 ===
try:
    from mafia_core import (
        GameState, Phase,
        create_default_game, mafia_kill,
        tally_votes_plurality, check_win
    )
except Exception as e:
    st.error(f"mafia_core import 오류: {e}")
    st.stop()

try:
    # llm_engine.llm_step(gs, player_input, goal) 를 가정
    from llm_engine import llm_step
except Exception:
    llm_step = None  # LLM 미사용시 안전장치

# =========================
# 페이지 설정 & 다크 스타일
# =========================
st.set_page_config(page_title="마피아 (Ollama EEVE)", page_icon="🕵️", layout="wide")

DARK_CSS = """
<style>
:root, .stApp, .main { background: #0d0f13 !important; color: #e6e6e6 !important; }
.block-container { max-width: 980px !important; }
.role-pill {
  font-size: 12px; padding: 2px 8px; border-radius: 999px; background: #1b2230; border:1px solid #2b3447; color:#b7c2d8;
}
.divider { height: 1px; background:#222831; margin: 12px 0; }
.audio-hint { font-size:12px; color:#88a; }
</style>
"""
st.markdown(DARK_CSS, unsafe_allow_html=True)

# ============= BGM (auto) =============
if "bgm_unmuted" not in st.session_state:
    st.session_state.bgm_unmuted = False

def bgm_html(unmuted: bool) -> str:
    # 정적파일: .streamlit/static/bgm.mp3  ->  /bgm.mp3 로 접근
    return f"""
    <audio id="bgm" autoplay loop {"muted" if not unmuted else ""} style="display:none">
      <source src="/bgm.mp3" type="audio/mpeg">
    </audio>
    <script>
      const a=document.getElementById('bgm');
      if(a) {{
        a.play().catch(()=>{{ /* autoplay block 무시 */ }});
      }}
    </script>
    """

st.markdown(bgm_html(st.session_state.bgm_unmuted), unsafe_allow_html=True)
colA, colB = st.columns([1, 6])
with colA:
    if st.button(
        "🔊 Unmute" if not st.session_state.bgm_unmuted else "🔇 Mute",
        use_container_width=True,
        key="btn_bgm_toggle"
    ):
        st.session_state.bgm_unmuted = not st.session_state.bgm_unmuted
        st.rerun()
with colB:
    st.markdown(
        '<div class="audio-hint">브라우저 정책으로 자동재생이 막히면 Unmute를 한번 눌러 주세요.</div>',
        unsafe_allow_html=True
    )

# ===================== 세션 상태 =====================
def _init_game():
    gs = create_default_game()
    state = {
        "gs": gs,
        "phase": "NIGHT",
        "first_night_done": False,
        "day_start_idx": 0,
    }
    if not hasattr(gs, "dialogue_history"):
        gs.dialogue_history = []
    return state

if "state" not in st.session_state:
    st.session_state.state = _init_game()

S = st.session_state.state
gs: GameState = S["gs"]

# ===================== 유틸 =====================
FALLBACKS = [
    "(주위를 살핀다.)","(눈을 피한다.)","(작게 한숨을 쉰다.)",
    "(입술을 깨문다.)","(아무 말 없이 분위기를 살핀다.)",
    "(잠시 침묵이 흐른다...)","(의심스러운 표정으로 주변을 바라본다.)",
]
GENERIC = {
    "저도 마찬가지입니다.","동의합니다.","단결해서 이 위기를 헤쳐나가자구요.",
    "항상 경계심을 가져야 합니다.","조심해야 해요.","믿고 단합합시다."
}

def append_dialog(name: str, text: str):
    gs.dialogue_history.append(f"{name}: {text}")

def alive_ai() -> List[str]:
    return [n for n in gs.alive_players() if n != "당신"]

def vote_targets() -> List[str]:
    return [n for n in gs.alive_players() if n != "당신"]

def anti_repeat(name: str, line: str, last_by_name: Dict[str, str]) -> str:
    norm = (line or "").strip()
    if (not norm) or (norm in GENERIC) or (norm == last_by_name.get(name, "")):
        return random.choice(FALLBACKS)
    return norm

def render_chat_box_from_gs(height=460, width_px=820):
    """gs.dialogue_history를 중앙 고정 채팅 카드로 랜더링"""
    lines = []
    for line in gs.dialogue_history[-200:]:
        if ": " in line:
            name, text = line.split(": ", 1)
        else:
            name, text = "사회자", line
        lines.append((name, text))

    css = f"""
    <style>
      .chat-wrap {{
        width: 100%;
        display: flex; justify-content: center; align-items: flex-start;
      }}
      .chat-card {{
        width: {width_px}px; max-width: 95vw; height: {height}px;
        background: #0f1116; border: 1px solid #2a2f3a; border-radius: 16px;
        padding: 14px; overflow: auto; box-shadow: 0 4px 20px rgba(0,0,0,.35);
      }}
      .msg {{ display: grid; grid-template-columns: 92px 1fr; gap: 8px; margin: 8px 0; }}
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
          <div class="msg {cls}"><div class="name">{name}</div><div class="bubble">{text}</div></div>
        ''')
    html = f"""
    {css}
    <div class="chat-wrap">
      <div id="chat-box" class="chat-card">
        {''.join(body)}
      </div>
    </div>
    <script>
      const box = document.getElementById('chat-box');
      if (box) {{ box.scrollTop = box.scrollHeight; }}
    </script>
    """
    components.html(html, height=height+40, scrolling=False)

def names_in(text: str, candidates: List[str]) -> List[str]:
    """문장 내에서 후보 이름 등장 카운트용 매칭(간단 문자열 포함기반)"""
    if not text: return []
    found = []
    for n in candidates:
        if n in text:
            found.append(n)
    return found

def mention_counts_today(candidates: List[str]) -> Dict[str, int]:
    """
    오늘 낮 시작 이후(= S['day_start_idx'] 이후) 대화에서
    '당신'이 쓴 문장 속에 등장한 이름 카운트.
    """
    start = S.get("day_start_idx", 0)
    cnt = {c: 0 for c in candidates}
    for line in gs.dialogue_history[start:]:
        if not line.startswith("당신: "):
            continue
        _, text = line.split(": ", 1)
        for n in names_in(text, candidates):
            cnt[n] += 1
    return cnt

# ========================= Sidebar: 상태판 =========================
with st.sidebar:
    st.markdown("### 🕵️ 마피아 (Ollama EEVE)")
    st.markdown(f'<span class="role-pill">Round {gs.round}</span>', unsafe_allow_html=True)
    st.write(f"**Role:** {gs.players['당신'].role}")
    st.write("**Alive:** " + ", ".join(gs.alive_players()))
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    if c1.button("🔁 새 판 시작", use_container_width=True, key="btn_new_game"):
        st.session_state.state = _init_game()
        st.rerun()
    if c2.button("🧹 로그 지우기", use_container_width=True, key="btn_clear_log"):
        gs.dialogue_history.clear()
        st.rerun()

# ========================= 헤더 =========================
st.markdown("## 🕵️ 마피아 게임")
st.caption("Dark UI / 중앙 채팅 / 턴 진행 / 투표 버튼 / BGM")

st.markdown(
    f"**Round {gs.round}** &nbsp;|&nbsp; **Role:** {gs.players['당신'].role} &nbsp;|&nbsp; "
    f"**Alive:** {', '.join(gs.alive_players())}"
)

# ========================= 채팅 박스 =========================
render_chat_box_from_gs(height=480, width_px=820)
st.markdown('<div class="divider"></div>', unsafe_allow_html=True)

# ========================= 턴 진행 로직 =========================
def do_night():
    # 첫 밤은 조용히 스킵
    if not S["first_night_done"]:
        append_dialog("사회자", "어둠이 내려앉고, 모두가 서로의 기척을 탐색합니다. (첫 밤은 조용히 지나갑니다)")
        S["first_night_done"] = True
        S["phase"] = "DAY_DISCUSS"
        # 오늘 낮 집계 시작점
        S["day_start_idx"] = len(gs.dialogue_history)
        return

    # 둘째 밤부터 마피아 킬
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
    S["day_start_idx"] = len(gs.dialogue_history)

def do_discuss(user_text: Optional[str]):
    last_by_name: Dict[str, str] = {}
    if user_text and user_text.strip():
        append_dialog("당신", user_text.strip())

    if llm_step is not None:
        try:
            out = llm_step(gs, user_text or "(...)", "discuss") or {}
            lines = []
            for it in out.get("character_lines", []):
                name = it.get("name"); line = it.get("line")
                if name in alive_ai() and line:
                    lines.append((name, line))
            # 중복/상투 방지 & 누락 보강
            spoken = set()
            for n, l in lines:
                fixed = anti_repeat(n, l, last_by_name)
                append_dialog(n, fixed)
                last_by_name[n] = fixed
                spoken.add(n)
            for n in alive_ai():
                if n not in spoken:
                    append_dialog(n, random.choice(FALLBACKS))
        except Exception:
            for n in alive_ai():
                append_dialog(n, random.choice(FALLBACKS))
    else:
        for n in alive_ai():
            append_dialog(n, random.choice(FALLBACKS))

def do_vote(user_choice: str):
    targets = vote_targets()
    allowed = targets + ["무처형"]
    if user_choice not in allowed:
        user_choice = "무처형"

    # AI 표: 오늘 낮 동안 '당신' 발언에서 이름 언급 빈도 기반
    counts = mention_counts_today(targets)
    ranked = sorted(targets, key=lambda x: (-counts.get(x, 0), x))
    ai_pick = ranked[0] if ranked else (targets[0] if targets else "무처형")

    votes: Dict[str, str] = {"당신": user_choice}
    for v in targets:
        votes[v] = ai_pick

    executed, counter = tally_votes_plurality(
        votes, gs.alive_players(), allow_no_lynch=True, no_lynch_label="무처형"
    )

    # 득표 출력
    lines = [f"{k}: {v}표" for k, v in sorted(counter.items(), key=lambda x: (-x[1], x[0]))]
    append_dialog("사회자", "🧮 득표 현황\n- " + "\n- ".join(lines))

    if executed:
        append_dialog("사회자", f"✅ 처형: {executed}")
        gs.players[executed].alive = False
        if hasattr(gs, "log"):
            gs.log.append(f"낮 투표로 {executed}이(가) 처형되었습니다.")
    else:
        append_dialog("사회자", "❎ 무처형")
        if hasattr(gs, "log"):
            gs.log.append("낮 투표 무처형.")

    w = check_win(gs)
    if w:
        append_dialog("사회자", "🎉 시민 팀 승리!" if w == "CITIZEN_WIN" else "💀 마피아 팀 승리!")
        st.stop()

    # 다음 라운드
    gs.round += 1
    S["phase"] = "NIGHT"

# ========================= 하단 입력/버튼 =========================
if S["phase"] == "NIGHT":
    st.subheader(f"🌙 밤 {gs.round}")
    col1, col2 = st.columns([1, 3])
    if col1.button("밤 진행", use_container_width=True, key="btn_night_go"):
        do_night()
        st.rerun()
    col2.button(
        "다음 단계로",
        use_container_width=True,
        key="btn_night_next",
        on_click=lambda: (do_night(), st.rerun())
    )

elif S["phase"] == "DAY_DISCUSS":
    st.subheader("☀️ 낮 토론")
    user_text = st.chat_input("한 줄 입력 후 Enter", key="ci_day_discuss")
    if user_text is not None:
        do_discuss(user_text)
        st.rerun()
    st.markdown('<div class="divider"></div>', unsafe_allow_html=True)
    if st.button("🗳 투표 단계로 이동", use_container_width=True, key="btn_to_vote"):
        S["phase"] = "DAY_VOTE"
        st.rerun()

elif S["phase"] == "DAY_VOTE":
    st.subheader("🗳 투표")
    targets = vote_targets()
    # 전체 생존자 + 무처형
    options = targets + ["무처형"]
    idx = 0 if targets else len(options) - 1
    choice = st.radio(
        "대상 선택", options=options, horizontal=False, index=idx, key="radio_vote_target"
    )
    col1, col2 = st.columns([1, 3])
    if col1.button("투표 실행", use_container_width=True, key="btn_cast_vote"):
        do_vote(choice)
        st.rerun()
    if col2.button("토론으로 돌아가기", use_container_width=True, key="btn_back_to_discuss"):
        S["phase"] = "DAY_DISCUSS"
        st.rerun()
