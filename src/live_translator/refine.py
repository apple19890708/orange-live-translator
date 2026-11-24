"""直播後精聽：用錄音離線重新辨識（30 秒視窗、前後文完整、最仔細的設定），產出比即時字幕更準的雙語字幕，
並和當場的即時字幕比對，列出不一樣的句子（自動校準的素材）。

  run.bat refine logs\\live-20261123-180716.pcm
  run.bat refine 音檔.m4a --no-translate

輸出（和輸入同一個資料夾、同檔名）：
  *.refined.srt          精聽版雙語字幕
  *.refined.md           和即時字幕（同名 .srt）的比對報告
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .asr.filters import clean_text, is_hallucination
from .config import apply_corrections, load_config, load_corrections, load_glossary
from .events import SubtitleEvent
from .segmenter import split_sentences
from .subtitles.formats import _ts


@dataclass
class Line:
    start: float
    end: float
    source: str
    target: str = ""


def transcribe_file(audio: np.ndarray, cfg, engine=None, progress=None) -> list[Line]:
    """離線辨識整段音訊，回傳依句號切好的句子。"""
    from .asr.streamer import WhisperEngine

    rcfg = dict(cfg.get("refine") or {})
    acfg = dict(cfg.asr)
    if engine is None:
        engine = WhisperEngine(acfg)
    lang = rcfg.get("language") or (acfg.get("language") if acfg.get("language") not in (None, "auto") else "ja")
    # 實測（tuning/experiments.md E50）：整段離線辨識時，提示詞與範例句都會引發幻聽迴圈（CER 51～361%），預設關閉
    hotwords = engine.hotwords.get(lang) if rcfg.get("hotwords", False) else None
    segments, info = engine.model.transcribe(
        audio,
        language=lang,
        beam_size=int(rcfg.get("beam_size", 5)),
        temperature=[0.0, 0.2, 0.4],
        condition_on_previous_text=bool(rcfg.get("condition_on_previous_text", True)),
        initial_prompt=rcfg.get("initial_prompt") or None,
        hotwords=hotwords or None,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": int(rcfg.get("vad_min_silence_ms", 500))},
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        # 字級時間會改變解碼結果，實測 CER 4.5%→5.0%（E52），預設關閉；改用下方的長度上限修正時間
        word_timestamps=bool(rcfg.get("word_timestamps", False)),
        hallucination_silence_threshold=rcfg.get("hallucination_silence_threshold")
        if rcfg.get("word_timestamps", False) else None,
    )
    lines: list[Line] = []
    total = audio.size / 16000
    for s in segments:
        if progress:
            progress(s.end, total)
        words = list(s.words or [])
        if not words:
            text = clean_text(s.text)
            if not text or is_hallucination(text):
                continue
            # 片段的開始時間準，但結尾常把後面的靜音也算進去（例：一句話跨 60 秒）。
            # 用「每字約 0.2 秒＋1 秒」當上限，再依字數分給各句。
            parts = split_sentences(text, lang) or [text]
            n = sum(len(p) for p in parts) or 1
            dur = min(s.end - s.start, n * 0.2 + 1.0 * len(parts))
            t = s.start
            for p in parts:
                e = t + dur * len(p) / n
                lines.append(Line(t, e, p))
                t = e
            continue
        # 依字的時間組句：遇到「。！？」就斷句
        cur, start = "", None
        for w in words:
            if start is None:
                start = w.start
            cur += w.word
            if w.word.strip()[-1:] in "。！？!?":
                text = clean_text(cur)
                if text and not is_hallucination(text):
                    lines.append(Line(start, w.end, text))
                cur, start = "", None
        if cur.strip():
            text = clean_text(cur)
            if text and not is_hallucination(text):
                lines.append(Line(start, words[-1].end, text))
    return lines


def translate_lines(lines: list[Line], translator, cfg) -> None:
    from .translate.worker import TranslationWorker

    w = TranslationWorker(translator, lambda e: None, int(cfg.translate.get("context_sentences", 4)))
    for i, ln in enumerate(lines):
        res = w._translate([SubtitleEvent(i, "final", ln.source, start=ln.start, end=ln.end, lang="ja")])
        ln.target = res[0].target if res and res[0].state == "translated" else ""


def write_srt(lines: list[Line], path: Path) -> None:
    blocks = []
    for i, ln in enumerate(lines, 1):
        text = f"{ln.target}\n{ln.source}" if ln.target else ln.source
        blocks.append(f"{i}\n{_ts(ln.start)} --> {_ts(ln.end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")


_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)")


def read_live_srt(path: Path) -> list[Line]:
    """讀即時字幕紀錄（第三行起：譯文、原文）。"""
    out = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")):
        rows = [r for r in block.strip().splitlines() if r.strip()]
        if len(rows) < 3:
            continue
        m = _SRT_TIME.search(rows[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        target, source = (rows[2], rows[3]) if len(rows) >= 4 else ("", rows[2])
        out.append(Line(start, end, source, target))
    return out


_NORM = re.compile(r"[\s、。！？!?,.・「」…ー〜]")


def word_diffs(live_text: str, refined_text: str, min_len: int = 2) -> list[tuple[str, str]]:
    """逐字比對，回傳「即時寫法 → 精聽寫法」不一樣的片段（忽略標點、長音、1 個字的小差異）。"""
    import difflib

    a, b = _NORM.sub("", live_text), _NORM.sub("", refined_text)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        x, y = a[i1:i2], b[j1:j2]
        if max(len(x), len(y)) < min_len:
            continue
        # 前後各補 2 個字，看得出是哪個詞
        out.append((a[max(0, i1 - 2):i2 + 2], b[max(0, j1 - 2):j2 + 2]))
    return out


def _flatten(lines: list[Line]) -> tuple[str, list[int]]:
    """全部句子接成一串（去標點），並記下每個字屬於第幾句。"""
    chars, owner = [], []
    for i, ln in enumerate(lines):
        for ch in ln.source:
            if not _NORM.match(ch):
                chars.append(ch)
                owner.append(i)
    return "".join(chars), owner


def compare(live: list[Line], refined: list[Line], max_len: int = 12) -> list[tuple[Line, str, str, list[Line]]]:
    """整份逐字對齊（不受兩邊斷句不同影響），只回傳「同一個位置聽成不同字」的片段。
    太長的差異（多半是某一邊漏聽或多聽一整段）不列，避免雜訊。"""
    import difflib

    a, a_owner = _flatten(live)
    b, b_owner = _flatten(refined)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op != "replace":
            continue
        if not (2 <= max(i2 - i1, j2 - j1) <= max_len):
            continue
        r = refined[b_owner[j1]]
        mine = sorted({a_owner[k] for k in range(i1, i2)})
        out.append((r, a[max(0, i1 - 3):i2 + 3], b[max(0, j1 - 3):j2 + 3], [live[k] for k in mine]))
    return out


def write_report(path: Path, src: Path, refined: list[Line], live: list[Line] | None, secs: float) -> None:
    lines = [f"# 直播後精聽報告：{src.name}", "",
             f"- 精聽句數：{len(refined)}；處理時間：{secs / 60:.1f} 分鐘"]
    if live is None:
        lines.append("- 找不到同名的即時字幕紀錄（.srt），只輸出精聽字幕。")
    else:
        diffs = compare(live, refined)
        lines += [f"- 即時字幕句數：{len(live)}；聽成不同字的地方：{len(diffs)}", "",
                  "兩邊在同一個位置聽成不同的字。精聽不一定對（例如曾把整理券聽成生理券），",
                  "這是「可能聽錯」的候選，要累積多場、重複出現才適合加進修正清單。", "",
                  "| 時間 | 即時 | 精聽 | 精聽整句 | 精聽譯文 | 即時譯文 |", "|---|---|---|---|---|---|"]
        for r, x, y, mine in diffs:
            lines.append(f"| {_ts(r.start)[:8]} | {x} | {y} | {r.source} | {r.target} | "
                         f"{' '.join(m.target for m in mine)} |".replace("\n", " "))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_refine(src: str, config_path: str | None = None, translate: bool = True) -> Path:
    from .asr.streamer import WhisperEngine
    from .audio.file_source import load_audio

    cfg = load_config(config_path)
    gpath = cfg.translate.get("glossary")
    glossary, corrections = load_glossary(gpath), load_corrections(gpath)
    path = Path(src)
    t0 = time.perf_counter()
    audio = load_audio(str(path))
    print(f"[refine] {path.name}：{audio.size / 16000 / 60:.1f} 分鐘")
    engine = WhisperEngine(dict(cfg.asr))
    engine.add_hotwords("ja", [k for k in glossary if any(ord(c) > 0x3000 for c in k)])

    def progress(done, total):
        print(f"\r[refine] 辨識 {done / total * 100:5.1f}%", end="", flush=True)

    lines = transcribe_file(audio, cfg, engine, progress)
    print()
    for ln in lines:
        ln.source = apply_corrections(ln.source, corrections)
    engine.close()
    if translate:
        from .translate import build_translator

        translator = build_translator(cfg.translate, glossary)
        print(f"[refine] 翻譯 {len(lines)} 句…")
        translate_lines(lines, translator, cfg)
        translator.unload()
    out_srt = path.with_suffix(".refined.srt")
    write_srt(lines, out_srt)
    live_srt = path.with_suffix(".srt")
    live = read_live_srt(live_srt) if live_srt.exists() else None
    report = path.with_suffix(".refined.md")
    write_report(report, path, lines, live, time.perf_counter() - t0)
    print(f"[refine] 完成 {(time.perf_counter() - t0) / 60:.1f} 分鐘 → {out_srt}、{report}")
    return out_srt
