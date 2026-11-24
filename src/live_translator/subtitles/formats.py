"""字幕格式：解析 YouTube json3、輸出 SRT。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass


@dataclass
class Cue:
    start: float
    end: float
    source: str
    target: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def parse_json3(data: str | bytes | dict) -> list[Cue]:
    if not isinstance(data, dict):
        data = json.loads(data)
    cues: list[Cue] = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs or ev.get("aAppend"):
            continue
        text = "".join(s.get("utf8", "") for s in segs).replace("\n", " ").strip()
        if not text:
            continue
        start = ev.get("tStartMs", 0) / 1000
        end = start + ev.get("dDurationMs", 0) / 1000
        cues.append(Cue(start, end, text))
    # 自動字幕的事件會重疊（捲動顯示），把結束時間截到下一句開始
    for a, b in zip(cues, cues[1:]):
        if a.end > b.start:
            a.end = b.start
    return [c for c in cues if c.end > c.start]


def merge_short(cues: list[Cue], min_dur: float = 1.2, max_chars: int = 40, sep: str = "") -> list[Cue]:
    """把太短、太碎的自動字幕合併，減少翻譯時斷句不完整。"""
    out: list[Cue] = []
    for c in cues:
        if out:
            p = out[-1]
            gap = c.start - p.end
            if (p.end - p.start < min_dur) and gap < 0.3 and len(p.source) + len(c.source) <= max_chars:
                p.source = f"{p.source}{sep}{c.source}"
                p.end = c.end
                continue
        out.append(Cue(c.start, c.end, c.source, c.target))
    return out


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(cues: list[Cue], bilingual: bool = True) -> str:
    blocks = []
    for i, c in enumerate(cues, 1):
        text = c.target or c.source
        if bilingual and c.target and c.target != c.source:
            text = f"{c.target}\n{c.source}"
        blocks.append(f"{i}\n{_ts(c.start)} --> {_ts(c.end)}\n{text}\n")
    return "\n".join(blocks)
