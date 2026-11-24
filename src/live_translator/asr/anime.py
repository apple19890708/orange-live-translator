"""anime-whisper（litagin/anime-whisper）：用遊戲配音資料微調的日文辨識模型，擅長動畫腔、情緒化台詞、笑聲喘氣。

用原版 transformers 執行（社群轉換的 faster-whisper 版本有問題）。依模型說明：
- 不要給前文提示詞（會幻聽、品質大降），所以不使用 prompt / hotwords。
- no_repeat_ngram_size=5 防止重複幻聽。
- 句尾「。」通常省略；數字、英文為半形。
介面和 WhisperEngine 相同，可以直接替換。只支援日文。
"""
from __future__ import annotations

import time

import numpy as np

from .filters import clean_text, is_hallucination

DEFAULT_REPO = "litagin/anime-whisper"


class AnimeWhisperEngine:
    def __init__(self, cfg):
        import torch
        from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

        self.cfg = cfg
        self.language = "ja"
        self.current_lang = "ja"
        self.hotwords: dict[str, str] = {}
        self.torch = torch
        repo = cfg.get("anime_model") or DEFAULT_REPO
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        t0 = time.perf_counter()
        print(f"[asr] 載入 anime-whisper {repo}（{self.device}）…")
        self.processor = AutoProcessor.from_pretrained(repo)
        from transformers.utils import logging as hf_logging

        hf_logging.set_verbosity_error()
        hf_logging.disable_progress_bar()
        self.model = AutoModelForSpeechSeq2Seq.from_pretrained(repo, dtype=self.dtype)
        self.model.to(self.device).eval()
        print(f"[asr] 載入完成 {time.perf_counter() - t0:.1f}s")

    def set_language(self, lang: str) -> None:
        pass  # 只支援日文

    def add_hotwords(self, lang: str, words) -> None:
        pass  # 模型作者建議不要給提示

    def detect(self, audio: np.ndarray) -> str:
        return "ja"

    def transcribe(self, audio: np.ndarray, final: bool, prompt: str | None = None,
                   lang: str | None = None) -> str:
        torch = self.torch
        feats = self.processor(audio.astype(np.float32, copy=False), sampling_rate=16000,
                               return_tensors="pt").input_features.to(self.device, self.dtype)
        with torch.inference_mode():
            ids = self.model.generate(
                feats, language="ja", task="transcribe",
                num_beams=int(self.cfg.get("beam_size", 5)) if final else 1,
                no_repeat_ngram_size=5, max_new_tokens=220, max_length=None,
            )
        text = clean_text(self.processor.batch_decode(ids, skip_special_tokens=True)[0])
        return "" if is_hallucination(text) else text

    def close(self) -> None:
        """切換到別的辨識模型時釋放顯示記憶體。"""
        self.model = None
        if self.device == "cuda":
            self.torch.cuda.empty_cache()
