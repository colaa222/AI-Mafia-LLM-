# main.py
import json, re, random, requests
from typing import Optional, List, Tuple, Dict
from mafia_core import (
    GameState, Phase, create_default_game,
    mafia_kill, tally_votes_plurality, check_win, top_two_mentions
)

# --- Ollama 설정 ---
MODEL_NAME = "EEVE-Korean-10.8B"
OLLAMA_URL = "http://localhost:11434/api/chat"

SYSTEM_PROMPT = """너는 마피아 게임의 사회자다.
항상 '단 하나의 JSON'만 출력한다. 설명/코드펜스/여분 텍스트 금지.
"""

FALLBACKS = [
    "(주위를 살핀다.)",
    "(눈을 피한다.)",
    "(작게 한숨을 쉰다.)",
    "(입술을 깨문다.)",
    "(아무 말 없이 분위기를 살핀다.)",
    "(잠시 침묵이 흐른다...)",
    "(의심스러운 표정으로 주변을 바라본다.)",
]
GENERIC_PHRASES = {
    "저도 마찬가지입니다.", "동의합니다.", "단결해서 이 위기를 헤쳐나가자구요.",
    "항상 경계심을 가져야 합니다.", "조심해야 해요.", "믿고 단합합시다."
}

# --- LLM 호출(JSON 강제 추출) ---
def llm_call_json(prompt: str) -> dict:
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "options": {"temperature": 0.4},
            },
            timeout=60,
        )
    except Exception:
        return {}
    text = ""
    try:
        text = r.json().get("message", {}).get("content", "")
    except Exception:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}

def _anti_repeat_line(gs: GameState, name: str, line: str) -> str:
    last = gs.last_line_by_name.get(name, "")
    norm = (line or "").strip()
    if (not norm) or (norm in GENERIC_PHRASES) or (norm == last):
        return random.choice(FALLBACKS)
    return norm

def _ensure_all_speak(gs: GameState, pairs: List[Tuple[str,str]]) -> List[Tuple[str,str]]:
    spoken = {n for n,_ in pairs}
    out = []
    for n, l in pairs:
        fixed = _anti_repeat_line(gs, n, l)
        out.append((n, fixed))
    for name in gs.alive_players():
        if name == "당신": continue
        if name not in spoken:
            out.append((name, random.choice(FALLBACKS)))
    return out

def day_discuss(gs: GameState, user_msg: str):
    summary = gs.to_summary_json()
    prompt = f"""
[게임요약]
{summary}

[플레이어 발언]
{user_msg}

[요청]
summary.alive_ai의 각 이름이 최근 대화(dialogue_recent)와 플레이어 발언을 참고하여
'마피아 추리/반박 중심' 1문장씩 발언해라. 인사/상투 표현 금지.
형식은 JSON만 허용:
{{"character_lines":[{{"name":"민수","line":"..."}}]}}
"""
    data = llm_call_json(prompt)
    pairs: List[Tuple[str,str]] = []
    for item in data.get("character_lines", []):
        name = item.get("name"); line = item.get("line")
        if name in gs.alive_players() and name != "당신" and line:
            pairs.append((name, line))

    pairs = _ensure_all_speak(gs, pairs)

    for name, line in pairs:
        print(f"{name}: {line}")
        gs.dialogue_history.append(f"{name}: {line}")
        gs.last_line_by_name[name] = line

def ai_votes_json(gs: GameState, allowed_choices: List[str]) -> dict:
    summary = gs.to_summary_json()
    choice_str = ", ".join(allowed_choices)
    prompt = f"""
[게임요약]
{summary}

[투표 제한 선택지]
{choice_str}

[요청]
각 AI는 위의 '투표 제한 선택지' 중 하나에만 1표를 던진다.
'무처형'은 처형을 원치 않을 때 선택한다.
형식은 JSON만 허용:
{{"votes":[{{"voter":"민수","target":"{allowed_choices[0]}"}}]}}
"""
    return llm_call_json(prompt)

def _fallback_ai_votes(gs: GameState, allowed_choices: List[str]) -> Dict[str,str]:
    """LLM이 표를 덜 낸 경우 보정: 모든 살아있는 AI가 제한 선택지에서 1표씩."""
    alive = gs.alive_players()
    voters = [n for n in alive if n != "당신"]
    # 간단 선호: Top1 > Top2 > 무처형
    pref = allowed_choices[:]  # 예: ["민수","지연","무처형"]
    votes: Dict[str,str] = {}
    for v in voters:
        # 첫 선택지로 통일(간단 규칙). 원하면 랜덤화 가능.
        votes[v] = pref[0] if pref else "무처형"
    return votes

def ai_votes_by_rule(gs: GameState, allowed_choices: List[str]) -> Dict[str, str]:
    """
    규칙: 이번 낮 '언급 Top1' 후보로 AI 전원 몰표.
    - 오늘 낮의 대화 로그(gs.dialogue_history 중 이번 낮의 범위)에서
      각 후보 이름의 언급 빈도를 세어 최다 언급 대상에게 몰표.
    - 동률이면 사전순으로 앞선 이름.
    - 후보가 하나도 없으면 allowed_choices[0] 또는 '무처형'.
    """
    voters = [n for n in gs.alive_players() if n != "당신"]
    if not voters:
        return {}

    # 오늘 낮 대화 범위 가져오기 (mafia_core의 start_new_day()가 범위 기준점을 찍는다고 가정)
    # 범위 유실 대비: 그냥 전체 dialogue_history를 사용해도 동작하도록 방어
    today_lines = getattr(gs, "dialogue_history", []) or []

    # 후보만 카운트 (무처형 제외)
    candidates = [c for c in allowed_choices if c != "무처형"]
    counts: Dict[str, int] = {c: 0 for c in candidates}

    # 단순 문자열 포함으로 언급량 세기 (정확 매칭 필요하면 정규식 경계 사용 가능)
    for line in today_lines:
        for name in candidates:
            if name and (name in line):
                counts[name] += 1

    # 랭킹
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    if ranked and ranked[0][1] > 0:
        top1 = ranked[0][0]
    else:
        # 언급이 전혀 없으면 첫 후보(있으면) 또는 무처형
        top1 = candidates[0] if candidates else "무처형"

    if top1 not in allowed_choices:
        top1 = "무처형"

    return {v: top1 for v in voters}



def print_tally(counter: Dict[str,int]):
    if not counter:
        print("\n🧮 득표 현황: (표 없음)")
        return
    print("\n🧮 득표 현황:")
    for name, cnt in sorted(counter.items(), key=lambda x: (-x[1], x[0])):
        print(f" - {name}: {cnt}표")

def main():
    print("🕵️ 마피아 게임 (터미널 / Ollama EEVE)\n")
    gs: GameState = create_default_game()

    while True:
        # ---------- 밤 ----------
        gs.phase = Phase.NIGHT
        print(f"\n라운드 {gs.round} 시작!")
        print(f"당신의 직업: {gs.players['당신'].role}")
        print("※ 이번 판 직업 구성: MAFIA 1명 + CITIZEN 6명 (정체 비공개)")
        print("생존자:", ", ".join(gs.alive_players()))
        print("-" * 50)

        print(f"\n🌙 [밤 {gs.round}]")
        killed = mafia_kill(gs)
        if killed:
            if killed == "당신":
                print(f"💥 밤에 {killed}이(가) 마피아에게 사망했습니다.")
                print("\n💀 마피아 팀 승리!\n")
                print("📝 게임 로그 (최근 30줄)")
                for x in gs.log[-30:]: print("-", x)
                return
            else:
                print(f"💥 밤에 {killed}이(가) 마피아에게 사망했습니다.")
        else:
            if gs.round == 1:
                print("… 첫 밤은 아무 일도 없이 지나갔습니다.")

        win = check_win(gs)
        if win:
            print("\n🎉 시민 팀 승리!\n" if win == "CITIZEN_WIN" else "\n💀 마피아 팀 승리!\n")
            print("📝 게임 로그 (최근 30줄)")
            for x in gs.log[-30:]: print("-", x)
            return

        # ---------- 낮 토론 ----------
        gs.phase = Phase.DAY_DISCUSS
        gs.start_new_day()  # ✅ “이번 낮” 집계 범위 시작점 찍기

        turns = 0
        print(f"\n☀️ [낮 토론] (명령어: /vote 로 투표 단계)")
        while True:
            try:
                user = input("당신: ").strip()
            except EOFError:
                user = "/vote"
            if user.lower().strip().startswith("/vote"):
                break
            if user == "":
                user = "(...)"
            gs.dialogue_history.append(f"당신: {user}")
            print()  # 가독성
            day_discuss(gs, user)
            turns += 1
            if turns >= 3:
                print("\n(최대 턴 도달. /vote 로 넘어가거나 한 줄 더 쓰고 /vote 입력)")

        # ---------- 낮 투표(최다득표) ----------
        # ---------- 낮 투표(최다득표) ----------
        gs.phase = Phase.DAY_VOTE
        alive_people = gs.alive_players()
        targets = [n for n in alive_people if n != "당신"]
        
        # === (투표 단계) 선택지 구성: 전체 생존자 + 무처형 ===
        allowed_choices = targets + ["무처형"]
        
        print("\n🗳 [투표] 대상:", ", ".join(targets))
        your = input("당신의 투표: ").strip()
        if your not in allowed_choices:
            print("⛔️ 잘못된 선택입니다. 이번 라운드 당신의 표는 '무처형'으로 처리됩니다.")
            your = "무처형"
        
        # 당신 표 반영
        vote_dict: Dict[str, str] = {"당신": your}
        
        # ✅ 규칙 기반: 이번 낮 ‘언급 Top1’로 AI 전원 몰표 (allowed_choices 안에서만)
        rule_votes = ai_votes_by_rule(gs, allowed_choices)
        for v, t in rule_votes.items():
            if v in targets and t in allowed_choices:
                vote_dict[v] = t
        
        # 최다득표(동률/무처형 최다는 무처형) 집계
        executed, counter = tally_votes_plurality(
            vote_dict, alive_people, allow_no_lynch=True, no_lynch_label="무처형"
        )
        
        print_tally(counter)
        if executed:
            print(f"✅ 처형: {executed}")
            gs.players[executed].alive = False
            gs.log.append(f"낮 투표로 {executed}이(가) 처형되었습니다.")
        else:
            print("❎ 무처형")
            gs.log.append("낮 투표 무처형.")
        


        win = check_win(gs)
        if win:
            print("\n🎉 시민 팀 승리!\n" if win == "CITIZEN_WIN" else "\n💀 마피아 팀 승리!\n")
            print("📝 게임 로그 (최근 30줄)")
            for x in gs.log[-30:]: print("-", x)
            return

        gs.round += 1

if __name__ == "__main__":
    main()
