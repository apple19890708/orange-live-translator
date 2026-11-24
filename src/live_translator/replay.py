"""依影片時間快取字幕（往回拉時間軸時字幕一致）。

問題：同一段直播往回拉再看一次，辨識是從拉到的位置重新開始切句，前文也不同，
所以原文、譯文都可能和第一次不一樣（I15）。

做法：
1. 擴充功能每秒（以及拉時間軸、暫停、播放時）回報「影片 ID＋播放時間」。
   伺服器記下當時已收到多少聲音（stream 秒數），兩者對起來就能把字幕的 stream 時間換成影片時間。
2. 翻好的句子依影片時間存起來。
3. 之後播到已經翻過的範圍：辨識結果大部分落在快取範圍內的句子不再翻譯、不顯示，
   改在播到那個時間點時送出當時存下的字幕。

只對 Chrome 分頁聽譯（有影片時間）生效；系統聲音、音檔沒有影片時間，一律照常處理。
"""
from __future__ import annotations

import bisect
import threading
from dataclasses import dataclass, field

from .events import SubtitleEvent

STALE_S = 10.0      # 超過這麼久沒有新的時間回報，就不再推算影片時間
EDGE_S = 0.25       # 判斷「播到某句」時的容許誤差


@dataclass
class Anchor:
    stream_t: float          # 收到回報時，伺服器已收到的聲音秒數
    video_id: str
    media_t: float           # 影片播放時間
    paused: bool = False
    rate: float = 1.0
    ad: bool = False         # 廣告播放中（影片時間是廣告的）
    seek: bool = False       # 剛拉過時間軸


@dataclass
class Cue:
    mstart: float
    mend: float
    source: str
    target: str
    lang: str = ""
    key: int = field(default=0)


class MediaReplay:
    def __init__(self, min_cover: float = 0.5, max_anchors: int = 4000, max_cues: int = 5000):
        self.min_cover = min_cover
        self.max_anchors = max_anchors
        self.max_cues = max_cues
        self._lock = threading.Lock()
        self._anchors: list[Anchor] = []
        self._times: list[float] = []
        self._cues: dict[str, list[Cue]] = {}
        self._by_event: dict[int, tuple[str, Cue]] = {}
        self._emitted: set[int] = set()     # 這一輪播放已送出的快取字幕
        self._replayed_ids: set[int] = set()
        self._next_key = 1
        self.suppressed = 0                 # 統計：省下沒重翻的句子
        self.replayed = 0

    # ---- 影片時間 ----
    def anchor(self, stream_t: float, video_id: str, media_t: float, paused: bool = False,
               rate: float = 1.0, ad: bool = False, seek: bool = False) -> None:
        a = Anchor(float(stream_t), str(video_id or ""), float(media_t), bool(paused),
                   float(rate or 1.0), bool(ad), bool(seek))
        with self._lock:
            if self._times and a.stream_t < self._times[-1]:
                a.stream_t = self._times[-1]
            prev = self._anchors[-1] if self._anchors else None
            if prev is not None and (prev.video_id != a.video_id or prev.ad != a.ad):
                a.seek = True
            if a.seek:
                self._emitted.clear()   # 拉過時間軸：重新開始一輪播放
            self._anchors.append(a)
            self._times.append(a.stream_t)
            if len(self._anchors) > self.max_anchors:
                drop = len(self._anchors) - self.max_anchors
                del self._anchors[:drop]
                del self._times[:drop]

    def _index_at(self, stream_t: float) -> int:
        return bisect.bisect_right(self._times, stream_t) - 1

    def _media_at(self, stream_t: float) -> tuple[str, float] | None:
        i = self._index_at(stream_t)
        if i < 0:
            return None
        a = self._anchors[i]
        dt = stream_t - a.stream_t
        if a.ad or not a.video_id or dt > STALE_S:
            return None
        return a.video_id, a.media_t + (0.0 if a.paused else dt * a.rate)

    def media_at(self, stream_t: float) -> tuple[str, float] | None:
        with self._lock:
            return self._media_at(stream_t)

    def _media_range(self, start: float, end: float) -> tuple[str, float, float] | None:
        i, j = self._index_at(start), self._index_at(end)
        if i < 0:
            return None
        # 句子中間拉過時間軸、換影片或插廣告：這句的影片時間不可靠
        if any(a.seek or a.ad for a in self._anchors[i + 1:j + 1]):
            return None
        s, e = self._media_at(start), self._media_at(end)
        if not s or not e or s[0] != e[0] or e[1] < s[1]:
            return None
        return s[0], s[1], e[1]

    # ---- 快取 ----
    def _coverage(self, vid: str, ms: float, me: float) -> float:
        cues = self._cues.get(vid) or []
        if me - ms <= 0:
            return 1.0 if any(c.mstart - EDGE_S <= ms <= c.mend + EDGE_S for c in cues) else 0.0
        got = sum(max(0.0, min(me, c.mend) - max(ms, c.mstart)) for c in cues)
        return min(1.0, got / (me - ms))

    def _cue_at(self, vid: str, t: float) -> Cue | None:
        for c in self._cues.get(vid) or []:
            if c.mstart - EDGE_S <= t <= c.mend + EDGE_S:
                return c
        return None

    def allow(self, ev: SubtitleEvent) -> bool:
        """辨識器的事件要不要照常處理。False：這段已經翻過，改用快取的字幕。"""
        with self._lock:
            if ev.state == "partial":
                # 句子講完後等停頓的那段時間也會送辨識中的字，時間已經超過快取那句的結尾，
                # 所以和定稿一樣看「整段有多少落在已翻過的範圍」
                r = self._media_range(ev.start, ev.end)
                if r and self._coverage(*r) >= self.min_cover:
                    return False
                m = self._media_at(ev.end)
                return not (m and self._cue_at(*m))
            if ev.state == "final":
                r = self._media_range(ev.start, ev.end)
                if r and self._coverage(*r) >= self.min_cover:
                    self.suppressed += 1
                    return False
            return True

    def on_event(self, ev: SubtitleEvent) -> None:
        """訂閱事件匯流排：翻好的句子依影片時間存起來；被收回的句子移除。"""
        with self._lock:
            if ev.id in self._replayed_ids:
                return
            if ev.state == "retract":
                hit = self._by_event.pop(ev.id, None)
                if hit:
                    vid, cue = hit
                    lst = self._cues.get(vid) or []
                    if cue in lst:
                        lst.remove(cue)
                return
            if ev.state != "translated" or not ev.target:
                return
            r = self._media_range(ev.start, ev.end)
            if not r:
                return
            vid, ms, me = r
            old = self._by_event.get(ev.id)
            if old and old[1] in (self._cues.get(vid) or []):
                old[1].target = ev.target        # 同一句重翻（例：先顯示「翻譯中」）
                return
            if self._coverage(vid, ms, me) >= self.min_cover:
                return
            cue = Cue(ms, me, ev.source, ev.target, ev.lang, self._next_key)
            self._next_key += 1
            lst = self._cues.setdefault(vid, [])
            keys = [c.mstart for c in lst]
            lst.insert(bisect.bisect_right(keys, ms), cue)
            if len(lst) > self.max_cues:
                lst.pop(0)
            self._by_event[ev.id] = (vid, cue)
            self._emitted.add(cue.key)           # 這一輪已經顯示過（現場翻的）

    def due(self, stream_t: float) -> list[Cue]:
        """播到哪些快取字幕了（每一輪播放每句只送一次）。"""
        with self._lock:
            m = self._media_at(stream_t)
            if not m:
                return []
            vid, t = m
            out = []
            for c in self._cues.get(vid) or []:
                if c.mstart - EDGE_S > t:
                    break
                if c.key not in self._emitted and c.mstart - EDGE_S <= t < c.mend:
                    self._emitted.add(c.key)
                    out.append(c)
            return out

    def mark_replayed(self, event_id: int) -> None:
        with self._lock:
            self._replayed_ids.add(event_id)
            self.replayed += 1
