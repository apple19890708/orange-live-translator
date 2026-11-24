"""字幕事件與事件匯流排（給浮動視窗、WebSocket、SRT 紀錄共用）。"""
from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Callable


@dataclass
class SubtitleEvent:
    id: int
    state: str              # "partial"（辨識中）/ "final"（定稿，尚未翻譯）/ "translated"
                            # "drop"（取消辨識中）/ "retract"（收回已顯示的句子：被合併到下一句重聽）
    source: str             # 日文原文
    target: str = ""        # 中文譯文
    start: float = 0.0      # 相對於開始擷取的秒數
    end: float = 0.0
    lang: str = ""          # 原文語言 ja / en
    type: str = field(default="subtitle")

    def to_dict(self) -> dict:
        return asdict(self)


Listener = Callable[[SubtitleEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Listener) -> None:
        with self._lock:
            self._listeners.append(fn)

    def publish(self, ev: SubtitleEvent) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(ev)
            except Exception as e:  # 一個顯示端壞掉不影響其他
                print(f"[events] listener error: {e!r}")
