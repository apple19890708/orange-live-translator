"""多段辨識基準：公告參考稿（A）＋鈐 Mae 4 部的日文標準答案，同時量「即時模式」與「直播後精聽」的 CER。

  .venv\\Scripts\\python.exe tuning\\eval_asr_multi.py
  .venv\\Scripts\\python.exe tuning\\eval_asr_multi.py --set refine.hotwords=true --only refine

即時模式套用修正清單（使用者實際看到的就是修正後的字）。只評答案涵蓋的時段。
平均有兩種：各段平均（每段同等重要）與字數加權（長的段落影響較大）。
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

from eval_asr import cer, cer_ops, normalize  # noqa: E402
from eval_translation import set_value  # noqa: E402
from run_cases import recognize  # noqa: E402

from live_translator.asr.streamer import build_engine  # noqa: E402
from live_translator.audio.file_source import load_audio  # noqa: E402
from live_translator.config import apply_corrections, glossary_hotwords, load_config, load_corrections, load_glossary  # noqa: E402
from live_translator.refine import transcribe_file  # noqa: E402


def items():
    yield "公告A", HERE / "benchmark" / "utano_announce.pcm", [
        {"start": 0, "end": 1e9, "text": (HERE / "benchmark" / "utano_announce_ref.txt").read_text(encoding="utf-8")}]
    for p in sorted((HERE / "benchmark" / "mae").glob("*.ja.json")):
        vid = p.name.split(".")[0]
        yield vid, p.with_name(f"{vid}.pcm"), json.loads(p.read_text(encoding="utf-8"))
    # 對照組（沒調校過的主播，只驗證不調校）
    for p in sorted((HERE / "benchmark" / "heldout").glob("*.ja.json")):
        vid = p.name.split(".")[0]
        yield "對照:" + vid, p.with_name(f"{vid}.pcm"), json.loads(p.read_text(encoding="utf-8"))


def chunk_ops(lines, chunks):
    """逐段對齊（整段一起對齊太慢），加總錯字、漏字、多字。"""
    tot = [0, 0, 0, 0]
    for c in chunks:
        hyp = "".join(l.source for l in lines if c["start"] - 0.5 <= (l.start + l.end) / 2 <= c["end"] + 0.5)
        for k, v in enumerate(cer_ops(hyp, c["text"])):
            tot[k] += v
    return tot


def in_chunks(lines, chunks):
    out = []
    for c in chunks:
        sel = [l for l in lines if c["start"] - 0.5 <= (l.start + l.end) / 2 <= c["end"] + 0.5]
        out.append("".join(l.source for l in sel))
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--only", choices=["live", "refine"])
    args = ap.parse_args()
    cfg = load_config()
    for kv in args.set:
        k, _, v = kv.partition("=")
        set_value(cfg, k, v)
    gpath = cfg.translate.get("glossary")
    glossary, corrections = load_glossary(gpath), load_corrections(gpath)
    engine = build_engine(cfg.asr)
    engine.add_hotwords("ja", glossary_hotwords(cfg.asr, glossary))

    modes = [args.only] if args.only else ["live", "refine"]
    res = {m: [] for m in modes}
    for name, audio_path, chunks in items():
        audio = load_audio(str(audio_path))
        ref = "".join(c["text"] for c in chunks)
        n = len(normalize(ref))
        row = [f"{name:14s}"]
        for m in modes:
            if m == "live":
                lines = recognize(audio, cfg, engine, corrections)
            else:
                lines = transcribe_file(audio, cfg, engine)
                for l in lines:
                    l.source = apply_corrections(l.source, corrections)
            c = cer(in_chunks(lines, chunks), ref)
            s, d, i, nn = chunk_ops(lines, chunks)
            miss = (s + d) / max(1, nn)
            res[m].append((c, n, name.startswith("對照:"), miss))
            row.append(f"{m} {c * 100:5.1f}%（漏聽＋聽錯 {miss * 100:4.1f}%：錯 {s}、漏 {d}、多 {i}）")
        print("  ".join(row), flush=True)
    for m in modes:
        tuned = [(c, n) for c, n, h, _ in res[m] if not h]
        held = [(c, n) for c, n, h, _ in res[m] if h]
        miss_t = [x for _, _, h, x in res[m] if not h]
        miss_h = [x for _, _, h, x in res[m] if h]
        avg = sum(c for c, _ in tuned) / len(tuned)
        wavg = sum(c * n for c, n in tuned) / sum(n for _, n in tuned)
        extra = f"　對照組 {sum(c for c, _ in held) / len(held) * 100:.1f}%" if held else ""
        print(f"[{args.set or '目前設定'}] {m}：調校組 各段平均 {avg * 100:.1f}%　字數加權 {wavg * 100:.1f}%{extra}")
        # 調校組的答案是剪輯者字幕、會跳過一些話：主要看「漏聽＋聽錯」（不算答案沒收的字）
        mh = f"　對照組 {sum(miss_h) / len(miss_h) * 100:.1f}%" if miss_h else ""
        print(f"[{args.set or '目前設定'}] {m}：漏聽＋聽錯 調校組 {sum(miss_t) / len(miss_t) * 100:.1f}%{mh}")


if __name__ == "__main__":
    main()
