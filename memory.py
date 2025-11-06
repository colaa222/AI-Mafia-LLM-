from __future__ import annotations
import json, os
from typing import Dict, List
from collections import defaultdict

DEFAULT_PATH = "memory.json"

def _clean_str(s: str) -> str:
    """
    문자열 내 '고아 서러게이트(lone surrogate)' 등 문제 문자를 안전하게 제거.
    - UTF-8로 인코딩 시 에러나는 코드는 무시하고 다시 디코딩.
    - 가급적 정보 손실을 최소화하기 위해 'ignore' 사용.
    """
    if not isinstance(s, str):
        s = str(s)
    return s.encode("utf-8", "ignore").decode("utf-8", "ignore")

def _sanitize(obj):
    """
    JSON 저장 전에 전체 구조를 재귀적으로 순회하며 문자열을 정화한다.
    dict/list/tuple/set/str 지원.
    """
    if obj is None:
        return None
    if isinstance(obj, str):
        return _clean_str(obj)
    if isinstance(obj, dict):
        return { _sanitize(k): _sanitize(v) for k, v in obj.items() }
    if isinstance(obj, (list, tuple, set)):
        t = [ _sanitize(x) for x in obj ]
        # 원래 타입을 최대한 유지
        if isinstance(obj, tuple):
            return tuple(t)
        if isinstance(obj, set):
            return set(t)
        return t
    # 숫자/불리언 등은 그대로
    return obj

class MemoryStore:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        self.data = {
            "dialogue_history": [],
            "facts": {"dead": [], "revealed_roles": {}},
            "suspicions": {},   # "A->B": count
            "quietness": {},    # "이름": 0~1 (높을수록 조용)
            "votes": {},        # "Round1": [...]
            "round_summaries": {}  # "Round1": "..."
        }
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                # 파일 자체의 인코딩 문제 방지를 위해 errors='ignore'
                with open(self.path, "r", encoding="utf-8", errors="ignore") as f:
                    loaded = json.load(f)
                # 로드한 데이터도 한 번 sanitize
                self.data.update(_sanitize(loaded))
            except Exception:
                # 깨진 파일이면 조용히 무시하고 새로 씀
                pass

    def save(self):
        # 저장 직전에 전체 sanitize
        sanitized = _sanitize(self.data)
        # errors='ignore'로 파일 핸들 쓰기 중 에러 방지
        with open(self.path, "w", encoding="utf-8", errors="ignore") as f:
            json.dump(sanitized, f, ensure_ascii=False, indent=2)

    # --- 업데이트 API ---
    def append_dialogue(self, line: str, max_keep: int = 120):
        line = _clean_str(line)
        self.data["dialogue_history"].append(line)
        if len(self.data["dialogue_history"]) > max_keep:
            self.data["dialogue_history"] = self.data["dialogue_history"][-max_keep:]

    def add_votes(self, round_no: int, items: List[Dict[str, str]]):
        # 표 내부 문자열도 정화
        safe_items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            v = _clean_str(it.get("voter", ""))
            t = _clean_str(it.get("target", ""))
            if v and t:
                safe_items.append({"voter": v, "target": t})
        self.data["votes"][f"Round{round_no}"] = safe_items

    def mark_dead(self, name: str):
        name = _clean_str(name)
        if name not in self.data["facts"]["dead"]:
            self.data["facts"]["dead"].append(name)

    def reveal_role(self, name: str, role: str):
        name = _clean_str(name); role = _clean_str(role)
        self.data["facts"]["revealed_roles"][name] = role

    def update_suspicions(self, lines: List[str]):
        targets = ["민수", "지연", "현우", "수아", "하린", "태훈"]
        for l in lines:
            l = _clean_str(l)
            if ": " not in l:
                continue
            speaker, text = l.split(": ", 1)
            for cand in targets:
                if cand != speaker and cand in text and ("수상" in text or "의심" in text):
                    key = f"{speaker}->{cand}"
                    self.data["suspicions"][key] = self.data["suspicions"].get(key, 0) + 1

    def update_quietness(self, alive: List[str], recent_dialogues: List[str], window:int=20):
        window_lines = [_clean_str(l) for l in recent_dialogues[-window:]]
        from collections import defaultdict as _dd
        cnt = _dd(int)
        for l in window_lines:
            if ": " in l:
                cnt[l.split(": ",1)[0]] += 1
        total = sum(cnt.values()) or 1
        for n in alive:
            speak_ratio = cnt.get(n, 0) / total
            self.data["quietness"][n] = round(1.0 - speak_ratio, 3)

    def set_round_summary(self, round_no:int, summary:str, max_len:int=220):
        s = _clean_str(summary or "").strip()
        if len(s) > max_len:
            s = s[:max_len-1] + "…"
        self.data["round_summaries"][f"Round{round_no}"] = s

    def build_prompt_snapshot(self, alive: List[str], round_no:int) -> str:
        key = f"Round{round_no-1}"
        prev_sum = self.data["round_summaries"].get(key, "")
        snap = {
            "alive": alive,
            "dead": self.data["facts"]["dead"],
            "revealed_roles": self.data["facts"]["revealed_roles"],
            "top_suspicions": sorted(self.data["suspicions"].items(), key=lambda x:-x[1])[:5],
            "quietness": {k: self.data["quietness"].get(k, 0.5) for k in alive},
            "last_round_summary": prev_sum
        }
        # 반환 문자열도 안전화
        return json.dumps(_sanitize(snap), ensure_ascii=False)
