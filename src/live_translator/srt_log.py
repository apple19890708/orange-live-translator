"""把即時翻譯結果存成 SRT（logs/ 資料夾），方便事後回顧。

最後幾句先留在記憶體：短句可能會被「合併重聽」收回、換成合併後的版本，確定不會再變才寫進檔案。
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from .config import ROOT
from .events import SubtitleEvent
from .subtitles.formats import _ts

HOLD = 3  # 留在記憶體的句數


class SrtLogger:
    def __init__(self, directory: Path | None = None):
        d = directory or ROOT / "logs"
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / f"live-{datetime.now():%Y%m%d-%H%M%S}.srt"
        self.count = 0
        self._pending: list[SubtitleEvent] = []
        self._lock = threading.Lock()

    def on_event(self, ev: SubtitleEvent) -> None:
        with self._lock:
            if ev.state == "retract":
                self._pending = [p for p in self._pending if p.id != ev.id]
                return
            if ev.state != "translated":
                return
            self._pending = [p for p in self._pending if p.id != ev.id] + [ev]
            self._pending.sort(key=lambda p: p.start)
            while len(self._pending) > HOLD:
                self._write(self._pending.pop(0))

    def flush(self) -> None:
        with self._lock:
            for p in self._pending:
                self._write(p)
            self._pending = []

    def _write(self, ev: SubtitleEvent) -> None:
        self.count += 1
        block = f"{self.count}\n{_ts(ev.start)} --> {_ts(ev.end)}\n{ev.target}\n{ev.source}\n\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(block)
