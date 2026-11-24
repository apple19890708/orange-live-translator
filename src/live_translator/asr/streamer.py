"""串流辨識：VAD 切句 + Whisper。說話中定期輸出暫定結果（partial），一句結束輸出定稿（final）。"""
from __future__ import annotations

import queue
import threading
import time
from collections import deque
from typing import Callable

import numpy as np

from ..audio.capture import SAMPLE_RATE
from ..events import SubtitleEvent
from .filters import clean_text, is_hallucination, looks_incomplete
from .vad import SpeechDetector


class WhisperEngine:
    def __init__(self, cfg):
        from ..gpu import setup_cuda_dlls

        setup_cuda_dlls()
        from faster_whisper import WhisperModel

        self.cfg = cfg
        # language: auto（在 languages 之中自動判斷）或固定 ja / en
        self.language = cfg.get("language", "auto")
        self.allowed = list(cfg.get("languages") or ["ja", "en"])
        self.current_lang = self.allowed[0] if self.language == "auto" else self.language
        # 提示詞（人名、活動名、專有名詞）：幫 Whisper 把「整理券」聽成整理券而不是「整理犬」
        hw = cfg.get("hotwords") or {}
        self.hotwords = {lang: " ".join(words) for lang, words in hw.items() if words}
        t0 = time.perf_counter()
        print(f"[asr] 載入 Whisper {cfg['model']}（{cfg.get('device', 'cuda')}, {cfg.get('compute_type')}）…")
        self.model = WhisperModel(
            cfg["model"],
            device=cfg.get("device", "cuda"),
            compute_type=cfg.get("compute_type", "int8_float16"),
        )
        print(f"[asr] 載入完成 {time.perf_counter() - t0:.1f}s")

    def close(self) -> None:
        """切換到別的辨識模型時釋放顯示記憶體。"""
        self.model = None

    def add_hotwords(self, lang: str, words) -> None:
        merged = (self.hotwords.get(lang, "") + " " + " ".join(words)).split()
        self.hotwords[lang] = " ".join(dict.fromkeys(merged))

    def set_language(self, lang: str) -> None:
        self.language = lang
        if lang != "auto":
            self.current_lang = lang

    def detect(self, audio: np.ndarray) -> str:
        """只在允許的語言中挑機率最高的；把握不大時沿用上一句的語言（避免短句亂跳）。"""
        if self.language != "auto":
            return self.language
        if audio.size < 16000 * 1.0:
            return self.current_lang
        try:
            _lang, _p, all_probs = self.model.detect_language(audio)
        except Exception:
            return self.current_lang
        probs = {k: v for k, v in all_probs if k in self.allowed}
        if not probs:
            return self.current_lang
        best = max(probs, key=probs.get)
        total = sum(probs.values()) or 1.0
        if best != self.current_lang and probs[best] / total < 0.75:
            return self.current_lang
        self.current_lang = best
        return best

    def sentence_end(self, audio: np.ndarray, prompt: str | None, guard: float = 0.6) -> float | None:
        """快速辨識（含字的時間），回傳最後一個「。！？」結尾的時間點（秒）；離結尾太近或沒有則回傳 None。
        長句一直講不停時，用來切在句子結束的地方，而不是等到 12 秒硬切。"""
        lang = self.current_lang
        segments, _ = self.model.transcribe(
            audio, language=lang, beam_size=1, temperature=0.0,
            hotwords=self.hotwords.get(lang) or None, condition_on_previous_text=False,
            initial_prompt=prompt or None, vad_filter=False, word_timestamps=True,
        )
        limit = audio.size / SAMPLE_RATE - guard
        words = [w for s in segments for w in (s.words or [])]
        best = None
        for w, nxt in zip(words, words[1:]):
            # 句號後面要有一點停頓才算真的句子結束（「そう!だ!」這種緊接著的不切）
            if w.word.strip()[-1:] in "。！？!?" and w.end <= limit and nxt.start - w.end >= 0.12:
                best = w.end
        return best

    def transcribe(self, audio: np.ndarray, final: bool, prompt: str | None = None,
                   lang: str | None = None) -> str:
        lang = lang or self.current_lang
        audio = normalize_loudness(audio, self.cfg.get("normalize_dbfs"))
        segments, _info = self.model.transcribe(
            audio,
            language=lang,
            beam_size=int(self.cfg.get("beam_size", 5)) if final else 1,
            best_of=1,
            # 定稿時若結果重複／不可信，提高溫度重試（Whisper 原本的做法）
            temperature=list(self.cfg.get("temperatures", [0.0, 0.2, 0.4])) if final else 0.0,
            compression_ratio_threshold=float(self.cfg.get("compression_ratio_threshold", 2.4)),
            log_prob_threshold=float(self.cfg.get("log_prob_threshold", -1.0)),
            repetition_penalty=float(self.cfg.get("repetition_penalty", 1.0)),
            hotwords=self.hotwords.get(lang) or None,
            condition_on_previous_text=False,
            initial_prompt=prompt or None,
            vad_filter=False,
            without_timestamps=True,
            no_speech_threshold=0.6,
        )
        texts = []
        for s in segments:
            if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
                continue
            if s.compression_ratio > 2.6:
                continue
            texts.append(s.text)
        text = clean_text("".join(texts))
        return "" if is_hallucination(text) else text


ENGINES = {
    "whisper": "Whisper large-v3",
    "anime": "anime-whisper（動畫腔）",
    "reazon": "ReazonSpeech",
}


def normalize_loudness(audio: np.ndarray, target_dbfs, max_gain_db: float = 20.0) -> np.ndarray:
    """聲音太小時放大到 target_dbfs（只放大不縮小，最多 max_gain_db）。None = 不處理。"""
    if target_dbfs is None or audio.size == 0:
        return audio
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    if rms <= 1e-6:
        return audio
    gain_db = float(target_dbfs) - 20 * np.log10(rms)
    if gain_db <= 0:
        return audio
    gain = 10 ** (min(gain_db, max_gain_db) / 20)
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


def build_engine(cfg):
    """asr.engine: whisper（預設）、anime（anime-whisper，動畫配音腔）、reazon（ReazonSpeech）。"""
    kind = str(cfg.get("engine", "whisper")).lower()
    if kind == "reazon":
        from .reazon import ReazonEngine

        return ReazonEngine(cfg)
    if kind == "anime":
        from .anime import AnimeWhisperEngine

        return AnimeWhisperEngine(cfg)
    return WhisperEngine(cfg)


def quietest_point(audio: np.ndarray, win: float = 0.3, search_from: float = 0.45,
                   guard: float = 0.4) -> int:
    """在音訊的後半段（避開最後 guard 秒）找能量最低的 win 秒，回傳切點的取樣位置。
    句子太長時切在換氣、停頓處，比固定在第 N 秒硬切自然很多。"""
    n = audio.size
    w = int(win * SAMPLE_RATE)
    lo = int(n * search_from)
    hi = n - int(guard * SAMPLE_RATE) - w
    if hi <= lo or w <= 0:
        return n
    hop = int(0.05 * SAMPLE_RATE)
    best, best_e = n, float("inf")
    for start in range(lo, hi, hop):
        e = float(np.mean(audio[start:start + w] ** 2))
        if e < best_e:
            best, best_e = start + w // 2, e
    return best


class StreamingRecognizer:
    def __init__(
        self,
        cfg,
        audio_queue: "queue.Queue[np.ndarray]",
        on_event: Callable[[SubtitleEvent], None],
        engine: WhisperEngine | None = None,
    ):
        self.cfg = cfg
        self.q = audio_queue
        self.on_event = on_event
        self.engine = engine or build_engine(cfg)
        if str(cfg.get("vad_mode", "window")) == "stateful":
            from .vad import StreamingSpeechDetector  # 連續偵測（I16）

            self.vad = StreamingSpeechDetector(threshold=float(cfg.get("vad_threshold", 0.5)),
                                               end_threshold=(float(cfg.get("vad_end_threshold")) if cfg.get("vad_end_threshold") is not None else None),
                                               keep=str(cfg.get("vad_keep", "max")))
        else:
            self.vad = SpeechDetector(threshold=float(cfg.get("vad_threshold", 0.5)))
        self.min_silence = float(cfg.get("min_silence_ms", 600)) / 1000
        # 評分（快速送入）時設 True：即時播放佇列幾乎是空的、一定會找句號切；
        # 快速送入時佇列常塞車，「塞車就跳過」會讓結果隨電腦忙碌程度改變
        self.always_sentence_check = False
        self.max_utt = float(cfg.get("max_utterance_s", 12))
        self.partial_interval = float(cfg.get("partial_interval_s", 0.8))
        self.min_speech = float(cfg.get("min_speech_ms", 300)) / 1000
        # 句子看起來沒講完時，最多再等多久才切句（0 = 關閉）
        self.hold = float(cfg.get("incomplete_hold_ms", 1000)) / 1000
        # 句子超過 soft_max 秒後，短短換氣（soft_pause）就切，避免一直講不停時字幕拖很久才出來
        self.soft_max = float(cfg.get("soft_max_s", 6))
        self.soft_pause = float(cfg.get("soft_pause_ms", 250)) / 1000
        # 長句（超過 soft_max 秒）每隔幾秒找一次句號，切在句子結束處
        self.sentence_cut = bool(cfg.get("sentence_cut", True))
        self.sentence_check_every = float(cfg.get("sentence_check_every_s", 1.5))

        self._stop = threading.Event()
        self.paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_id = 1
        self._last_final = ""
        # 前文提示：最近幾句定稿，給 Whisper 當上下文（whisper_streaming 的做法），人名、話題詞比較不會聽錯
        self._recent: deque[str] = deque(maxlen=8)
        self.prompt_chars = int(cfg.get("prompt_chars", 60))
        # 放在提示詞最前面的範例句：Whisper 會模仿提示詞的寫法，給有標點的句子它就比較會加「、。？」
        self.prompt_seed = str(cfg.get("prompt_seed", "") or "")
        self.recorder = None  # 有設定時，把收到的音訊存成 .pcm 方便事後重播比對
        self.stream_time = 0.0  # 已處理音訊的秒數
        self.on_tick = None     # 每處理一段聲音呼叫一次 on_tick(stream_time)（在辨識執行緒內）
        # 短句合併重聽：上一句很短、而且下一句緊接著開始時，兩句一起重新辨識。
        # 句子在一個字中間被切開時（例：「ライブイン台北」切成「ライブイン タイ」＋「ペイ…」），合在一起才聽得對。
        self.merge_gap = float(cfg.get("merge_gap_s", 0))  # 預設關閉：實測整體錯誤率 6.6% → 7.6～8.0%
        self.merge_short = float(cfg.get("merge_short_s", 3.5))
        # 大聲說話時不算停頓：激動喊叫時 Silero VAD 常給接近 0 的機率（I16），句子收到一半就被當成結束或整段丟掉。
        # 已經在收一句時，音量 ≥ loud_rms 且 VAD 機率 ≥ loud_min_prob，就當作還在說話。0 = 關閉
        self.loud_rms = float(cfg.get("loud_rms", 0) or 0)
        self.loud_min_prob = float(cfg.get("loud_min_prob", 0.05))
        # 定稿太稀疏就不給前文重聽：前文提示偶爾讓 Whisper 跳過句子開頭，只寫出最後一小段（I16）
        self.retry_sparse = bool(cfg.get("retry_sparse", False))
        self.sparse_min_s = float(cfg.get("sparse_min_s", 5.0))
        self.sparse_cps = float(cfg.get("sparse_chars_per_s", 3.0))
        self._prev: dict | None = None

    def alloc_id(self) -> int:
        """給其他來源的字幕（例：依影片時間重播的快取字幕）一個和辨識結果同順序的 id。只能在 on_tick 裡呼叫。"""
        eid = self._next_id
        self._next_id += 1
        return eid

    def _tick(self) -> None:
        if self.on_tick is not None:
            try:
                self.on_tick(self.stream_time)
            except Exception as e:
                print(f"[asr] on_tick 錯誤：{e!r}")

    def _prompt(self) -> str:
        text = ""
        for s in reversed(self._recent):
            if len(text) + len(s) > self.prompt_chars:
                break
            text = s + text
        return (self.prompt_seed + text) if self.prompt_seed else text

    # ---- 執行緒控制 ----
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="asr", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ---- 主迴圈 ----
    def _run(self) -> None:
        preroll: deque[np.ndarray] = deque()
        preroll_len = 0
        PREROLL = int(0.3 * SAMPLE_RATE)
        buf: list[np.ndarray] = []
        buf_len = 0
        in_speech = False
        silence = 0.0
        speech = 0.0
        utt_start = 0.0
        last_partial = 0.0
        last_audio_wall = time.monotonic()
        pause_checked = False   # 這次停頓是否已判斷過「沒講完」
        soft_checked = False    # 這次短暫換氣是否已判斷過
        next_sentence_check = self.soft_max  # 句子長到幾秒時，檢查一次有沒有「。」可以切
        holding = False

        def finalize(split: bool = False, cut_at: int | None = None):
            """split=True：句子太長被迫切開時，切在後半段最安靜的地方，剩下的留給下一句。
            cut_at：直接指定切點（取樣位置），例如句號結束的地方。"""
            nonlocal buf, buf_len, in_speech, silence, speech, pause_checked, holding, utt_start, soft_checked
            nonlocal next_sentence_check
            next_sentence_check = self.soft_max
            audio = np.concatenate(buf) if buf else np.zeros(0, np.float32)
            rest = None
            if cut_at is not None and 0 < cut_at < audio.size:
                audio, rest = audio[:cut_at], audio[cut_at:]
            elif split and audio.size:
                cut = quietest_point(audio)
                audio, rest = audio[:cut], audio[cut:]
            eid = self._next_id
            self._next_id += 1
            if speech >= self.min_speech and audio.size:
                # 去掉結尾大段靜音，減少幻聽
                tail_cut = int(max(0.0, silence - 0.25) * SAMPLE_RATE)
                if 0 < tail_cut < audio.size:
                    audio = audio[: audio.size - tail_cut]
                prev_lang = self.engine.current_lang
                lang = self.engine.detect(audio)
                start_t, end_t = utt_start, utt_start + audio.size / SAMPLE_RATE
                p = self._prev
                if (p and self.merge_gap > 0 and lang == p["lang"]
                        and 0 <= start_t - p["end"] <= self.merge_gap
                        and p["end"] - p["start"] <= self.merge_short
                        and end_t - p["start"] <= self.max_utt + 2):
                    # 和上一句合併重聽；上一句的文字不要當提示詞（它可能就是聽錯的那句）
                    if self._recent and self._recent[-1] == p["text"]:
                        self._recent.pop()
                    gap = np.zeros(int((start_t - p["end"]) * SAMPLE_RATE), np.float32)
                    audio = np.concatenate([p["audio"], gap, audio])
                    start_t = p["start"]
                    self.on_event(SubtitleEvent(p["id"], "retract", p["text"], lang=lang))
                    self.merged_count = getattr(self, "merged_count", 0) + 1
                # 換語言時，上一句的提示詞反而會誤導
                prompt = self._prompt() if lang == prev_lang else None
                text = self.engine.transcribe(audio, final=True, prompt=prompt, lang=lang)
                dur = audio.size / SAMPLE_RATE
                if (self.retry_sparse and prompt and dur >= self.sparse_min_s
                        and len(text) < self.sparse_cps * dur):
                    alt = self.engine.transcribe(audio, final=True, prompt=None, lang=lang)
                    self.sparse_retries = getattr(self, "sparse_retries", 0) + 1
                    if len(alt) > 1.5 * len(text):
                        text = alt
                if text:
                    self._last_final = text
                    self._recent.append(text)
                    self._prev = {"id": eid, "audio": audio, "start": start_t, "end": end_t,
                                  "lang": lang, "text": text}
                    self.on_event(SubtitleEvent(eid, "final", text, start=start_t, end=end_t, lang=lang))
                else:
                    if p and start_t == p["start"]:  # 合併重聽失敗：原本那句放回去
                        self._recent.append(p["text"])
                        self.on_event(SubtitleEvent(p["id"], "final", p["text"], start=p["start"],
                                                    end=p["end"], lang=p["lang"]))
                    self._prev = None
                    self.on_event(SubtitleEvent(eid, "drop", ""))
            else:
                self.on_event(SubtitleEvent(eid, "drop", ""))
            pause_checked = holding = soft_checked = False
            if rest is not None and rest.size:
                # 剩下的半句接著聽，不重設 VAD
                utt_start += audio.size / SAMPLE_RATE
                buf, buf_len, in_speech = [rest], rest.size, True
                silence, speech = 0.0, rest.size / SAMPLE_RATE
                return
            buf, buf_len, in_speech, silence, speech = [], 0, False, 0.0, 0.0
            self.vad.reset()

        while not self._stop.is_set():
            try:
                chunk = self.q.get(timeout=0.2)
            except queue.Empty:
                # 沒有聲音輸入（暫停播放時 loopback 不會送資料）→ 當作靜音
                if in_speech and time.monotonic() - last_audio_wall >= self.min_silence:
                    silence = max(silence, self.min_silence)
                    finalize()
                continue
            last_audio_wall = time.monotonic()
            if self.recorder is not None:
                try:
                    self.recorder.write((np.clip(chunk, -1, 1) * 32767).astype("<i2").tobytes())
                except Exception:
                    self.recorder = None
            if self.paused.is_set():
                if in_speech:
                    finalize()
                self.stream_time += chunk.size / SAMPLE_RATE
                self._tick()
                continue

            dur = chunk.size / SAMPLE_RATE
            self.stream_time += dur
            self._tick()
            p = self.vad.feed(chunk)

            if not in_speech:
                preroll.append(chunk)
                preroll_len += chunk.size
                while preroll_len - preroll[0].size >= PREROLL:
                    preroll_len -= preroll.popleft().size
                if p >= self.vad.threshold:
                    in_speech = True
                    buf = list(preroll)
                    buf_len = preroll_len
                    preroll.clear()
                    preroll_len = 0
                    utt_start = self.stream_time - buf_len / SAMPLE_RATE
                    speech = dur
                    silence = 0.0
                    last_partial = time.monotonic()
                continue

            buf.append(chunk)
            buf_len += chunk.size
            loud = (self.loud_rms > 0 and p >= self.loud_min_prob
                    and float(np.sqrt(np.mean(chunk * chunk))) >= self.loud_rms)
            # 「還在說話」：連續偵測另外有門檻與取法（vad_end_threshold、vad_keep），其他偵測器就用同一個機率
            keep = getattr(self.vad, "keep", p)
            if keep >= getattr(self.vad, "end_threshold", self.vad.threshold) or loud:
                speech += dur
                silence = 0.0
                pause_checked = holding = soft_checked = False
            else:
                silence += dur

            if buf_len / SAMPLE_RATE >= self.max_utt:
                finalize(split=True)
                continue
            if (self.sentence_cut and buf_len / SAMPLE_RATE >= next_sentence_check
                    and hasattr(self.engine, "sentence_end") and (self.always_sentence_check or self.q.qsize() < 5)):
                # 一直講不停：找句號切開，前面那句先出字幕
                next_sentence_check = buf_len / SAMPLE_RATE + self.sentence_check_every
                t = self.engine.sentence_end(np.concatenate(buf), self._prompt())
                self.sentence_checks = getattr(self, "sentence_checks", 0) + 1
                if t is not None and t >= 1.0:
                    self.sentence_cuts = getattr(self, "sentence_cuts", 0) + 1
                    finalize(cut_at=int((t + 0.08) * SAMPLE_RATE))
                    continue
            if buf_len / SAMPLE_RATE >= self.soft_max and silence >= self.soft_pause and not soft_checked:
                # 長句遇到短暫換氣：先快速辨識，句子講完了才切（「〜のね」「〜て」這種講到一半的不切）
                soft_checked = True
                quick = self.engine.transcribe(np.concatenate(buf), final=False, prompt=self._prompt())
                if quick and not looks_incomplete(quick, self.engine.current_lang):
                    finalize()
                    continue
            if silence >= self.min_silence:
                if self.hold > 0 and not pause_checked and speech >= self.min_speech:
                    # 停頓了：快速辨識一次，看句子是不是講到一半（例如「〜て」「〜けど」「and」）
                    pause_checked = True
                    quick = self.engine.transcribe(np.concatenate(buf), final=False,
                                                   prompt=self._prompt())
                    holding = bool(quick) and looks_incomplete(quick, self.engine.current_lang)
                    if quick:
                        self.on_event(SubtitleEvent(self._next_id, "partial", quick,
                                                    start=utt_start, end=self.stream_time))
                if not holding or silence >= self.min_silence + self.hold:
                    finalize()
                continue

            # 只在佇列沒有積壓時做暫定辨識，避免延遲越拖越長
            if time.monotonic() - last_partial >= self.partial_interval and self.q.qsize() < 3:
                last_partial = time.monotonic()
                if speech >= self.min_speech:
                    text = self.engine.transcribe(np.concatenate(buf), final=False,
                                                  prompt=self._prompt())
                    if text:
                        self.on_event(SubtitleEvent(self._next_id, "partial", text,
                                                    start=utt_start, end=self.stream_time))
