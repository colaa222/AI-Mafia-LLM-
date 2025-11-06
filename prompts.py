from typing import Optional

SYSTEM_PROMPT = """
너는 마피아 게임의 사회자다.
항상 단 하나의 JSON만 출력한다. 코드펜스, 설명, 여분 텍스트 금지.

출력 예시:
{"character_lines":[
  {"name":"민수","line":"지연이 질문을 피했습니다. 수상합니다."},
  {"name":"하린","line":"(잠시 침묵하며 민수를 똑바로 본다.)"}
]}
"""

def build_user_prompt(summary: str, player_input: Optional[str], goal: str, memory_snapshot: str = "") -> str:
    goal = (goal or "").lower()

    if goal.startswith("night"):
        req = (
            "지금은 밤입니다. 분위기만 1문장으로 묘사하세요. "
            "특정 인물의 행동/의심/정보는 밝히지 마세요. "
            "JSON key는 narration 하나만 포함."
        )

    elif goal.startswith("discuss"):
        req = (
            "지금은 낮 토론입니다. summary.alive_ai 중 3~5명이 발언하거나 행동합니다. "
            "dialogue_recent, PLAYER_SAID, MEMORY_SNAPSHOT을 참고하여 "
            "① 의심 제기 ② 반박 ③ 자기 방어 ④ 짧은 행동묘사 를 만드세요. "
            "말을 아끼는 사람은 '(잠시 침묵한다)', '(눈을 피한다)' 등으로 표현할 수 있지만 "
            "**최소 2명은 반드시 문장 발언이어야 합니다.** "
            "행동 묘사는 40자 이내. "
            "금지: 인사/잡담/농담/선서/충성/협력 제안/분위기 환기/주제 제안. "
            "JSON key는 character_lines 하나만 포함하고, 각 항목은 {'name':'이름','line':'대사'}."
        )

    elif goal.startswith("vote"):
        req = (
            "지금은 투표 단계입니다. summary.alive_ai의 각 이름이 한 명을 지목합니다. "
            "actions.vote_results 배열로만 출력하고 JSON만 반환하세요."
        )

    else:
        req = "게임 종료. narration 1문장만 포함한 JSON으로 출력."

    return f"""
[GAME_SUMMARY]
{summary}

[MEMORY_SNAPSHOT]
{memory_snapshot}

[PLAYER_SAID]
{player_input or "없음"}

[REQUEST]
{req}
"""
