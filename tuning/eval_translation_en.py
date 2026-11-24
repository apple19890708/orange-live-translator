r"""英文翻譯基準：TED 演講的人工英文字幕 → 我們的翻譯 → 和人工繁中字幕比 chrF（只量翻譯，不經辨識）。

  .venv\Scripts\python.exe tuning\eval_translation_en.py                                  目前設定（英文用 translate.model）
  .venv\Scripts\python.exe tuning\eval_translation_en.py --set translate.model=hf.co/tencent/Hy-MT2-1.8B-GGUF:Q8_0

資料：tuning/benchmark/en/*.pairs.json（TED 志工翻譯的台灣繁中字幕，依時間配對）。整篇照順序翻（帶前文），整篇比 chrF。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from eval_translation import chrf, set_value  # noqa: E402

from live_translator.config import load_config, load_glossary  # noqa: E402
from live_translator.events import SubtitleEvent  # noqa: E402
from live_translator.translate import build_translator  # noqa: E402
from live_translator.translate.worker import TranslationWorker  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", default=[])
    args = ap.parse_args()
    cfg = load_config()
    for kv in args.set:
        k, _, v = kv.partition("=")
        set_value(cfg, k, v)
    translator = build_translator(cfg.translate, load_glossary(cfg.translate.get("glossary")))
    scores = []
    t0 = time.time()
    n = 0
    for p in sorted((HERE / "benchmark" / "en").glob("*.pairs.json")):
        pairs = json.loads(p.read_text(encoding="utf-8"))
        w = TranslationWorker(translator, lambda e: None, int(cfg.translate.get("context_sentences", 4)))
        outs = []
        for i, x in enumerate(pairs):
            res = w._translate([SubtitleEvent(i + 1, "final", x["en"], start=x["start"], end=x["end"], lang="en")])
            outs.append(res[0].target if res and res[0].state == "translated" else "")
        n += len(pairs)
        sc = chrf("".join(outs), "".join(x["zh"] for x in pairs))
        scores.append(sc)
        print(f"{p.name.split('.')[0]} chrF {sc:.1f}　例：{pairs[10]['en']} → {outs[10]}", flush=True)
    print(f"[{args.set or '目前設定'}] 英文平均 chrF {sum(scores) / len(scores):.1f}；每句 {(time.time() - t0) / n:.2f} 秒")
    translator.unload()


if __name__ == "__main__":
    main()
