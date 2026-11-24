"""依句號重新斷句：辨識結果是照「停頓」切的，不一定是完整句子。

- 一段裡有好幾句（有「。！？」）→ 拆成好幾行，各自翻譯，字幕比較好讀。
- 結尾剩下沒講完的短碎片（例：「…誰にも分からなくて今」的「今」）→ 先不翻，接到下一句前面；
  等太久（hold_s）下一句都沒來，就自己成一句。

事件 id：辨識器的第 N 句拆成 N*100+0、N*100+1…，排序和原本一致；辨識中（partial）用 N*100。
"""
from __future__ import annotations

import re
import threading
from typing import Callable

from .events import SubtitleEvent

_SPLIT_JA = re.compile(r"(?<=[。！？!?])")
_SPLIT_EN = re.compile(r"(?<=[.!?])\s+")
_TERMINAL = "。！？!?."


def split_sentences(text: str, lang: str) -> list[str]:
    parts = _SPLIT_EN.split(text) if lang == "en" else _SPLIT_JA.split(text)
    return [p.strip() for p in parts if p.strip()]


class Segmenter:
    def __init__(self, emit: Callable[[SubtitleEvent], None], hold_s: float = 2.5,
                 max_fragment: int = 8, enabled: bool = True, split: bool = True):
        self.emit = emit
        # split=False：一段裡有好幾句也不拆開（實測拆開會讓譯文缺上下文，見 tuning E38），只做「結尾碎片接下一句」
        self.split = split
        self.hold_s = hold_s
        self.max_fragment = max_fragment
        self.enabled = enabled
        self._subs: dict[int, list[int]] = {}   # 辨識器 id → 拆出來的 id
        self._held: SubtitleEvent | None = None   # 等著接到下一句的碎片
        self._timer: threading.Timer | None = None
        self._lock = threading.RLock()

    # ---- 對外 ----
    def feed(self, ev: SubtitleEvent) -> None:
        with self._lock:
            if not self.enabled:
                self.emit(ev)
                return
            base = ev.id * 100
            if ev.state in ("partial", "drop"):
                text = ev.source
                if ev.state == "partial" and self._held is not None:
                    text = self._held.source + text  # 辨識中的字幕也接上碎片，畫面才連貫
                self.emit(SubtitleEvent(base, ev.state, text, ev.target, ev.start, ev.end, ev.lang))
            elif ev.state == "retract":
                for sid in self._subs.pop(ev.id, [base]):
                    self.emit(SubtitleEvent(sid, "retract", ev.source, lang=ev.lang))
            elif ev.state == "final":
                self._final(ev, base)
            else:
                self.emit(ev)

    def flush(self) -> None:
        with self._lock:
            self._release_held()

    # ---- 內部 ----
    def _final(self, ev: SubtitleEvent, base: int) -> None:
        text, start = ev.source, ev.start
        if self._held is not None:
            held = self._held
            self._cancel_timer()
            self._held = None
            self.emit(SubtitleEvent(held.id, "drop", ""))
            if held.lang == ev.lang:
                text = held.source + text
                start = held.start
            else:
                self.emit(SubtitleEvent(held.id, "final", held.source, start=held.start,
                                        end=held.end, lang=held.lang))
        sentences = split_sentences(text, ev.lang) or [text]
        # 最後一段沒有句尾標點、又很短 → 先留著接下一句
        tail = None
        if len(sentences) > 1 and sentences[-1][-1] not in _TERMINAL and len(sentences[-1]) <= self.max_fragment:
            tail = sentences.pop()
        if not self.split and len(sentences) > 1:
            sentences = ["".join(sentences)]
        total = sum(len(s) for s in sentences) + (len(tail) if tail else 0) or 1
        dur = max(0.0, ev.end - start)
        ids, t = [], start
        for i, s in enumerate(sentences):
            seg_end = t + dur * len(s) / total
            sid = base + i
            ids.append(sid)
            self.emit(SubtitleEvent(sid, "final", s, start=t, end=seg_end, lang=ev.lang))
            t = seg_end
        if tail:
            sid = base + len(sentences)
            self._held = SubtitleEvent(sid, "partial", tail, start=t, end=ev.end, lang=ev.lang)
            self.emit(self._held)
            self._timer = threading.Timer(self.hold_s, self.flush)
            self._timer.daemon = True
            self._timer.start()
        self._subs[ev.id] = ids
        if len(self._subs) > 200:
            for k in list(self._subs)[:100]:
                del self._subs[k]

    def _release_held(self) -> None:
        self._cancel_timer()
        if self._held is not None:
            h = self._held
            self._held = None
            self.emit(SubtitleEvent(h.id, "final", h.source, start=h.start, end=h.end, lang=h.lang))

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
