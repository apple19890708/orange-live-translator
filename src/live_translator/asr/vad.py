"""人聲偵測：使用 faster-whisper 內建的 Silero VAD（ONNX，跑在 CPU，很輕）。

faster-whisper 的 SileroVADModel 每次呼叫都會重設內部狀態，所以每次餵入最近約 1 秒的音訊，
取最後幾個 32ms 視窗的最大機率，當作「現在有沒有人在說話」。
"""
from __future__ import annotations

import numpy as np

WINDOW = 512            # 16kHz 下 32ms
TAIL_WINDOWS = 30       # 約 1 秒
RECENT_WINDOWS = 4      # 看最後約 128ms


class SpeechDetector:
    def __init__(self, threshold: float = 0.5):
        from faster_whisper.vad import get_vad_model

        self.model = get_vad_model()
        self.threshold = threshold
        self._tail = np.zeros(0, np.float32)

    def reset(self) -> None:
        self._tail = np.zeros(0, np.float32)

    def feed(self, chunk: np.ndarray) -> float:
        """餵入新音訊，回傳目前的人聲機率 0～1。"""
        self._tail = np.concatenate([self._tail, chunk.astype(np.float32, copy=False)])
        max_len = WINDOW * TAIL_WINDOWS
        if self._tail.size > max_len:
            self._tail = self._tail[-max_len:]
        n = (self._tail.size // WINDOW) * WINDOW
        if n == 0:
            return 0.0
        probs = np.asarray(self.model(self._tail[-n:])).reshape(-1)
        k = min(RECENT_WINDOWS, max(1, chunk.size // WINDOW + 1))
        return float(probs[-k:].max())


class StreamingSpeechDetector:
    """連續偵測（I16）：每 32ms 依序送入並保留模型狀態，對激動喊叫比較不會漏。

    要用官方 silero-vad 套件附的模型（`pip install silero-vad --no-deps`）：
    熱千めら實測喊叫段判成人聲的比例 27% → 48%、正常說話 82% → 89%。
    faster-whisper 附的模型檔狀態格式不同，依序送入也沒有改善（26%），所以沒裝套件時會直接報錯。
    """

    CONTEXT = 64

    def __init__(self, threshold: float = 0.5, end_threshold: float | None = None, keep: str = "max"):
        import importlib.util
        import os

        import onnxruntime

        spec = importlib.util.find_spec("silero_vad")
        if spec is None or not spec.submodule_search_locations:
            raise RuntimeError("asr.vad_mode: stateful 需要 silero-vad 套件："
                               "在專案資料夾執行 .venv/Scripts/python.exe -m pip install silero-vad --no-deps")
        path = os.path.join(list(spec.submodule_search_locations)[0], "data", "silero_vad.onnx")
        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 4
        self.session = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=opts)
        self.threshold = threshold
        # 「開始說話」用 threshold＋最高機率（敏感，不漏喊叫）；「還在說話」另外判斷：
        # 這個模型在句間短停頓時機率仍偏高，用同一個門檻會讓句子拖長、字幕晚出（E63）
        self.end_threshold = threshold if end_threshold is None else end_threshold
        self.keep_mode = keep
        self.keep = 0.0
        self._sr = np.array(16000, dtype=np.int64)
        self._state = np.zeros((2, 1, 128), np.float32)
        self._ctx = np.zeros(self.CONTEXT, np.float32)
        self._rest = np.zeros(0, np.float32)
        self._last = 0.0

    def reset(self) -> None:
        """句子結束時不清狀態：這個模型本來就是一路串流下去用的。"""

    def feed(self, chunk: np.ndarray) -> float:
        """餵入新音訊，回傳這段音訊裡最高的人聲機率 0～1（判斷開始說話）；self.keep 判斷還在說話。"""
        x = np.concatenate([self._rest, chunk.astype(np.float32, copy=False)])
        n = x.size // WINDOW
        self._rest = x[n * WINDOW:]
        if n == 0:
            return self._last
        probs = []
        for i in range(n):
            w = x[i * WINDOW:(i + 1) * WINDOW]
            out, self._state = self.session.run(
                None, {"input": np.concatenate([self._ctx, w])[None, :], "state": self._state, "sr": self._sr})
            self._ctx = w[-self.CONTEXT:]
            probs.append(float(np.asarray(out).reshape(-1)[-1]))
        best = max(probs)
        self.keep = {"mean": float(np.mean(probs)), "last": probs[-1]}.get(self.keep_mode, best)
        self._last = best
        return best
