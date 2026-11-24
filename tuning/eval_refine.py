"""直播後精聽的評分：辨識 CER（基準 A）＋翻譯 chrF（基準 B），可直接和即時模式的數字比較。

  .venv\\Scripts\\python.exe tuning\\eval_refine.py
  .venv\\Scripts\\python.exe tuning\\eval_refine.py --set refine.condition_on_previous_text=false --set refine.hotwords=false
  .venv\\Scripts\\python.exe tuning\\eval_refine.py --asr-only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE.parent / "scripts"))

from eval_asr import cer  # noqa: E402
from eval_translation import chrf, set_value  # noqa: E402

from live_translator.asr.streamer import WhisperEngine  # noqa: E402
from live_translator.audio.file_source import load_audio  # noqa: E402
from live_translator.config import apply_corrections, glossary_hotwords, load_config, load_corrections, load_glossary  # noqa: E402
from live_translator.refine import transcribe_file, translate_lines  # noqa: E402
from live_translator.translate import build_translator  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--asr-only", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    for kv in args.set:
        k, _, v = kv.partition("=")
        set_value(cfg, k, v)
    gpath = cfg.translate.get("glossary")
    glossary, corrections = load_glossary(gpath), load_corrections(gpath)
    engine = WhisperEngine(dict(cfg.asr))
    engine.add_hotwords("ja", glossary_hotwords(cfg.asr, glossary))

    a = load_audio(str(HERE / "benchmark" / "utano_announce.pcm"))
    lines = transcribe_file(a, cfg, engine)
    hyp = "".join(apply_corrections(l.source, corrections) for l in lines)
    ref = (HERE / "benchmark" / "utano_announce_ref.txt").read_text(encoding="utf-8")
    print(f"[{args.set or '目前設定'}] 精聽 CER {cer(hyp, ref) * 100:.1f}%（即時模式 6.6%）", flush=True)
    if args.asr_only:
        return

    translator = build_translator(cfg.translate, glossary)
    scores = []
    for p in sorted((HERE / "benchmark" / "mae").glob("*.ref.json")):
        vid = p.name.split(".")[0]
        refs = json.loads(p.read_text(encoding="utf-8"))
        lines = transcribe_file(load_audio(str(p.with_name(f"{vid}.pcm"))), cfg, engine)
        lines = [l for l in lines if any(l.start < r["end"] + 1.5 and l.end > r["start"] - 1.5 for r in refs)]
        for l in lines:
            l.source = apply_corrections(l.source, corrections)
        translate_lines(lines, translator, cfg)
        s = chrf("".join(l.target for l in lines), "".join(r["text"] for r in refs))
        scores.append(s)
        print(f"  {vid} chrF {s:.1f}", flush=True)
    print(f"精聽翻譯 chrF 平均 {sum(scores) / len(scores):.1f}（即時模式 25.8）")
    translator.unload()


if __name__ == "__main__":
    main()
