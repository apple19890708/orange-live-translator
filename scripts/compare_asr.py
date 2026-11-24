"""比較語音辨識模型：同一段音訊，用完整的即時聽譯流程（VAD 切句、等句子講完、長句切在換氣處）跑一次，
輸出每個模型聽到的原文和耗時。

用法：
  .venv\\Scripts\\python.exe scripts\\compare_asr.py 音檔 --start 15 --end 105 -o docs\\asr-compare.md
"""
from __future__ import annotations

import argparse
import queue
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402

from live_translator.asr.streamer import StreamingRecognizer, WhisperEngine  # noqa: E402
from live_translator.audio.file_source import load_audio  # noqa: E402
from live_translator.config import load_config  # noqa: E402

SR = 16000


def run(model: str, audio: np.ndarray, cfg) -> tuple[list[tuple[float, float, str]], float, float]:
    acfg = dict(cfg.asr)
    acfg["model"] = model
    acfg["language"] = "ja"
    engine = WhisperEngine(acfg)
    q: "queue.Queue[np.ndarray]" = queue.Queue()
    finals: list[tuple[float, float, str]] = []
    final_times: list[float] = []
    r = StreamingRecognizer(acfg, q, lambda ev: ev.state == "final" and finals.append((ev.start, ev.end, ev.source)),
                            engine)
    # 只量定稿辨識耗時：包一層計時
    orig = engine.transcribe

    def timed(a, final, prompt=None, lang=None):
        t0 = time.perf_counter()
        out = orig(a, final, prompt, lang)
        if final:
            final_times.append(time.perf_counter() - t0)
        return out

    engine.transcribe = timed
    r.start()
    block = SR // 10
    for i in range(0, audio.size, block):
        q.put(audio[i:i + block])
        if i % (SR * 2) == 0:
            time.sleep(0.05)  # 讓辨識執行緒跟上，避免一次積太多
    q.put(np.zeros(SR * 2, np.float32))
    while not q.empty():
        time.sleep(0.2)
    time.sleep(2.5)
    r.stop()
    avg = sum(final_times) / len(final_times) if final_times else 0.0
    del engine
    return finals, avg, max(final_times or [0.0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float, default=90)
    ap.add_argument("-o", "--output", default="docs/asr-compare.md")
    ap.add_argument("--models", nargs="+", default=[
        "large-v3-turbo", "large-v3", "kotoba-tech/kotoba-whisper-v2.0-faster"])
    args = ap.parse_args()

    cfg = load_config()
    audio = load_audio(args.audio)[int(args.start * SR):int(args.end * SR)]
    md = ["# 語音辨識模型比較", "",
          f"音檔：`{Path(args.audio).name}` 第 {args.start:.0f}～{args.end:.0f} 秒，語言固定日文，"
          "同樣經過 VAD 切句、等句子講完、長句切在換氣處。", ""]
    summary = ["| 模型 | 句數 | 每句定稿平均 | 最慢 |", "|---|---|---|---|"]
    sections = []
    for m in args.models:
        print(f"===== {m} =====", flush=True)
        finals, avg, worst = run(m, audio, cfg)
        name = m.split("/")[-1]
        summary.append(f"| {name} | {len(finals)} | {avg:.2f}s | {worst:.2f}s |")
        sections += [f"## {name}", ""]
        for s, e, t in finals:
            line = f"- `{s + args.start:6.1f}s` {t}"
            sections.append(line)
            print(line, flush=True)
        sections.append("")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md + summary + [""] + sections), encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
