"""測試用：把音檔以接近即時的速度送進佇列，模擬 loopback 擷取。"""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

from .capture import SAMPLE_RATE


def load_audio(path: str) -> np.ndarray:
    """用 PyAV 解碼任意音檔／影片成 16kHz 單聲道 float32。
    （不用 faster_whisper.audio.decode_audio：它和新版 PyAV 不相容）"""
    if str(path).lower().endswith(".pcm"):  # 本程式存的錄音（16kHz 單聲道 16 位元）
        return np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0
    import av

    chunks = []
    with av.open(path) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


class FileSource:
    def __init__(self, path: str, speed: float = 1.0, block_ms: int = 100):
        self.path = path
        self.speed = speed
        self.block = int(SAMPLE_RATE * block_ms / 1000)
        self.queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=600)
        self.device_name = f"file:{path}"
        self.done = threading.Event()
        self._stop = threading.Event()

    def start(self) -> None:
        audio = load_audio(self.path)
        print(f"[audio] 讀取音檔 {self.path}（{audio.size / SAMPLE_RATE:.1f}s）")

        def run():
            t0 = time.monotonic()
            for i in range(0, audio.size, self.block):
                if self._stop.is_set():
                    break
                self.queue.put(audio[i:i + self.block])
                target = t0 + (i + self.block) / SAMPLE_RATE / self.speed
                delay = target - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
            # 結尾補一秒靜音讓最後一句定稿
            self.queue.put(np.zeros(SAMPLE_RATE, np.float32))
            self.done.set()

        threading.Thread(target=run, name="file-source", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
