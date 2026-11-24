"""ReazonSpeech（k2 / Zipformer，用 3.5 萬小時日本電視節目訓練的日文專用模型），透過 sherpa-onnx 執行。

介面和 WhisperEngine 相同（transcribe / detect / set_language / add_hotwords），可以直接替換。
只支援日文；不支援前文提示詞。
"""
from __future__ import annotations

import os
import time

import numpy as np

from .filters import clean_text, is_hallucination

DEFAULT_REPO = "reazon-research/reazonspeech-k2-v2"


class ReazonEngine:
    def __init__(self, cfg):
        import sherpa_onnx
        from huggingface_hub import snapshot_download

        self.cfg = cfg
        self.language = "ja"
        self.current_lang = "ja"
        self.hotwords: dict[str, str] = {}
        repo = cfg.get("model") if "reazon" in str(cfg.get("model", "")).lower() else DEFAULT_REPO
        t0 = time.perf_counter()
        print(f"[asr] 載入 ReazonSpeech {repo} …")
        path = snapshot_download(repo)
        q = "" if cfg.get("compute_type") == "float32" else ".int8"

        def f(name: str) -> str:
            return os.path.join(path, f"{name}-epoch-99-avg-1{q}.onnx")

        self.rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=f("encoder"), decoder=f("decoder"), joiner=f("joiner"),
            tokens=os.path.join(path, "tokens.txt"),
            num_threads=int(cfg.get("threads", 4)), sample_rate=16000, feature_dim=80,
            decoding_method="greedy_search",
        )
        print(f"[asr] 載入完成 {time.perf_counter() - t0:.1f}s")

    def close(self) -> None:
        self.rec = None

    def set_language(self, lang: str) -> None:
        pass  # 只支援日文

    def add_hotwords(self, lang: str, words) -> None:
        pass

    def detect(self, audio: np.ndarray) -> str:
        return "ja"

    def transcribe(self, audio: np.ndarray, final: bool, prompt: str | None = None,
                   lang: str | None = None) -> str:
        s = self.rec.create_stream()
        # Zipformer 轉換器在音訊結尾沒有靜音時會吃掉最後幾個字，前後補一點靜音
        pad = np.zeros(int(16000 * 0.5), np.float32)
        s.accept_waveform(16000, np.concatenate([pad[:4000], audio.astype(np.float32, copy=False), pad]))
        self.rec.decode_stream(s)
        text = clean_text(s.result.text)
        return "" if is_hallucination(text) else text
