"""分頁聲音來源：Chrome 擴充功能擷取指定分頁的聲音，以 16kHz 單聲道 Int16 PCM 經 WebSocket 傳來。"""
from __future__ import annotations

import queue

import numpy as np


class TabAudioSource:
    def __init__(self):
        self.queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=600)
        self.device_name = "Chrome 分頁（等待擴充功能開始聽譯）"
        self.active = False
        self.received_s = 0.0   # 已放進佇列的聲音秒數（和辨識器的 stream_time 同一個時間軸）

    def push_pcm16(self, data: bytes) -> None:
        if len(data) < 2:
            return
        x = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
        try:
            self.queue.put_nowait(x)
            self.received_s += x.size / 16000
        except queue.Full:
            pass

    def start(self) -> None:
        pass  # 資料由 WebSocket 推進來

    def stop(self) -> None:
        pass
