# mafia_core.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple
import json, random

class Phase(str, Enum):
    NIGHT = "NIGHT"
    DAY_DISCUSS = "DAY_DISCUSS"
    DAY_VOTE = "DAY_VOTE"
    END = "END"

@dataclass
class Player:
    name: str
    role: str   # "MAFIA" | "CITIZEN"
    alive: bool = True

@dataclass
class GameState:
    round: int = 1
    phase: Phase = Phase.NIGHT
    players: Dict[str, Player] = field(default_factory=dict)
    log: List[str] = field(default_factory=list)

    # 대화/토론 관리
    dialogue_history: List[str] = field(default_factory=list)
    last_line_by_name: Dict[str, str] = field(default_factory=dict)
    dialogue_round_start_idx: int = 0  # 이번 낮 토론이 시작된 인덱스(“이번 낮” 집계 창)

    # --- 유틸 ---
    def alive_players(self) -> List[str]:
        return [p.name for p in self.players.values() if p.alive]

    def alive_ai(self) -> List[str]:
        return [n for n in self.alive_players() if n != "당신"]

    def mafia_alive(self) -> int:
        return sum(1 for p in self.players.values() if p.alive and p.role == "MAFIA")

    def citizen_alive(self) -> int:
        return sum(1 for p in self.players.values() if p.alive and p.role == "CITIZEN")

    def to_summary_json(self) -> str:
        summary = {
            "round": self.round,
            "phase": self.phase,
            "alive": self.alive_players(),
            "dead": [p.name for p in self.players.values() if not p.alive],
            "alive_ai": self.alive_ai(),
            "dialogue_recent": self.dialogue_history[-12:],
        }
        return json.dumps(summary, ensure_ascii=False)

    def start_new_day(self) -> None:
        """이번 낮부터의 대화만 집계하도록 시작 인덱스 리셋."""
        self.dialogue_round_start_idx = len(self.dialogue_history)

def norm_name(s: Optional[str]) -> str:
    return (
        (s or "")
        .strip()
        .replace("\u200b", "")
        .replace("\ufeff", "")
        .replace("\u2060", "")
    )

def _count_name_in_line(name: str, line: str) -> int:
    """
    한국어 이름은 \b 경계가 잘 안 먹히므로, 단순 포함 횟수를 셉니다.
    한 줄 안에서 여러 번 나오면 그만큼 가산.
    """
    if not name or not line:
        return 0
    return line.count(name)

def mention_counts_for_today(gs: GameState) -> Dict[str, int]:
    """
    이번 낮 토론 창구(gs.dialogue_round_start_idx 이후)에서
    생존자('당신' 제외) 각 이름이 몇 번 '언급'됐는지 세어 dict로 반환.
    """
    window = gs.dialogue_history[gs.dialogue_round_start_idx:]
    alive = [n for n in gs.alive_players() if n != "당신"]
    counts = {n: 0 for n in alive}
    for line in window:
        for n in alive:
            counts[n] += _count_name_in_line(n, line)
    return counts


def get_current_mafia(gs: GameState) -> Optional[str]:
    for name, p in gs.players.items():
        if p.alive and p.role == "MAFIA":
            return name
    return None

def create_default_game() -> GameState:
    names = ["당신", "민수", "지연", "현우", "수아", "하린", "태훈"]
    gs = GameState()
    for n in names:
        gs.players[n] = Player(n, "CITIZEN")
    # 마피아 1명 랜덤 배정(‘당신’ 제외)
    ai_alive = [n for n in names if n != "당신"]
    pick = random.choice(ai_alive)
    gs.players[pick].role = "MAFIA"
    return gs

def mafia_kill(gs: GameState) -> Optional[str]:
    """마피아 밤 처치. 1라운드(첫 밤)는 항상 평화롭게 넘어감."""
    if gs.round == 1:
        gs.log.append("첫 밤은 평화롭게 지나갔습니다.")
        return None

    mafia_name = get_current_mafia(gs)
    if not mafia_name:
        return None
    alive = gs.alive_players()
    candidates = [n for n in alive if n != mafia_name]  # ‘당신’ 포함 허용
    if not candidates:
        return None

    # 간단 휴리스틱: 이번 낮 대화에서 언급 많이 된 대상 우선
    counts = {n: 0 for n in candidates}
    window = gs.dialogue_history[max(0, len(gs.dialogue_history) - 30):]
    for line in window:
        for n in candidates:
            if n in line:
                counts[n] += 1
    if any(counts.values()):
        top = max(counts.values())
        pool = [n for n, c in counts.items() if c == top]
        pick = random.choice(pool)
    else:
        pick = random.choice(candidates)

    if pick not in gs.players or not gs.players[pick].alive:
        return None

    gs.players[pick].alive = False
    gs.log.append(f"밤에 {pick}이(가) 사망했습니다.")
    return pick

def top_two_mentions(gs: GameState) -> List[str]:
    """
    이번 낮 토론에서 언급 횟수 상위 2명 (생존자, '당신' 제외).
    - 언급 0이면: 생존자에서 랜덤 보충
    - 항상 2명 반환 보장
    """
    alive = [n for n in gs.alive_players() if n != "당신"]
    if len(alive) <= 2:
        return alive

    counts = mention_counts_for_today(gs)
    ranked = sorted(alive, key=lambda x: (-counts.get(x, 0), x))
    top2 = [n for n in ranked if counts.get(n, 0) > 0][:2]

    if len(top2) < 2:
        pool = [n for n in alive if n not in top2]
        random.shuffle(pool)
        top2 += pool[: (2 - len(top2))]

    return top2


def tally_votes_plurality(
    v_dict: Dict[str, str],
    alive_people: List[str],
    allow_no_lynch: bool = True,
    no_lynch_label: str = "무처형",
) -> Tuple[Optional[str], Dict[str, int]]:
    """
    최다득표 처형.
    - allow_no_lynch=True 이고 무처형이 최다 → 무처형(=None)
    - 최다 동률 → 무처형
    """
    counter: Dict[str, int] = {}
    valid_targets = set(alive_people)
    for voter, target in v_dict.items():
        if target == no_lynch_label:
            counter[no_lynch_label] = counter.get(no_lynch_label, 0) + 1
        elif target in valid_targets:
            counter[target] = counter.get(target, 0) + 1
        # else: 무효표 무시

    if not counter:
        return None, {}

    top_cnt = max(counter.values())
    top_names = sorted([n for n, c in counter.items() if c == top_cnt])

    # 동률 → 무처형
    if len(top_names) != 1:
        return None, counter

    top_name = top_names[0]
    if allow_no_lynch and top_name == no_lynch_label:
        return None, counter

    executed = top_name if top_name in valid_targets else None
    return executed, counter

def check_win(gs: GameState) -> Optional[str]:
    """
    승리 조건
    - 마피아 전멸 → 시민 승
    - 시민 수 <= 마피아 수 → 마피아 승
    """
    m = gs.mafia_alive()
    c = gs.citizen_alive()
    if m == 0:
        return "CITIZEN_WIN"
    if m >= c:
        return "MAFIA_WIN"
    return None
