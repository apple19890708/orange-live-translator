"""Windows WASAPI loopback：擷取「電腦正在播放的聲音」，轉成 16kHz 單聲道 float32。"""
from __future__ import annotations

import queue
import time

import numpy as np
import soxr

SAMPLE_RATE = 16000


def list_loopback_devices() -> list[dict]:
    import pyaudiowpatch as pyaudio

    p = pyaudio.PyAudio()
    try:
        return list(p.get_loopback_device_info_generator())
    finally:
        p.terminate()


class LoopbackCapture:
    """開始後，16kHz float32 單聲道的音訊塊會一直放進 self.queue。"""

    def __init__(self, device_hint: str = "", block_ms: int = 100):
        self.device_hint = device_hint or ""
        self.block_ms = block_ms
        self.queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=600)
        self._pa = None
        self._stream = None
        self._resampler = None
        self._channels = 2
        self.device_name = ""
        self.started_at = 0.0

    def _find_device(self, pa) -> dict:
        import pyaudiowpatch as pyaudio

        loopbacks = list(pa.get_loopback_device_info_generator())
        if not loopbacks:
            raise RuntimeError("找不到 WASAPI loopback 裝置")
        if self.device_hint:
            for d in loopbacks:
                if self.device_hint.lower() in d["name"].lower():
                    return d
            print(f"[audio] 找不到名稱含「{self.device_hint}」的裝置，改用預設輸出")
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        if speakers.get("isLoopbackDevice"):
            return speakers
        for d in loopbacks:
            if speakers["name"] in d["name"]:
                return d
        return loopbacks[0]

    def _callback(self, in_data, frame_count, time_info, status):
        import pyaudiowpatch as pyaudio

        x = np.frombuffer(in_data, dtype=np.float32)
        if self._channels > 1:
            x = x.reshape(-1, self._channels).mean(axis=1)
        y = self._resampler.resample_chunk(x)
        if y.size:
            try:
                self.queue.put_nowait(y.astype(np.float32, copy=False))
            except queue.Full:
                pass  # 辨識跟不上時丟棄最舊以外的新資料，避免記憶體一直長
        return (None, pyaudio.paContinue)

    def start(self) -> None:
        import pyaudiowpatch as pyaudio

        self._pa = pyaudio.PyAudio()
        dev = self._find_device(self._pa)
        rate = int(dev["defaultSampleRate"])
        self._channels = max(1, int(dev["maxInputChannels"]))
        self._resampler = soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32")
        self.device_name = dev["name"]
        self._stream = self._pa.open(
            format=pyaudio.paFloat32,
            channels=self._channels,
            rate=rate,
            input=True,
            input_device_index=dev["index"],
            frames_per_buffer=int(rate * self.block_ms / 1000),
            stream_callback=self._callback,
        )
        self.started_at = time.monotonic()
        print(f"[audio] 擷取中：{self.device_name}（{rate}Hz, {self._channels}ch）")

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None
