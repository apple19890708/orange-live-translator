"""翻譯基準：用有人工中文字幕的影片（tuning/benchmark/mae/），跑完整流程（辨識→翻譯），和人工字幕比較。

  .venv\\Scripts\\python.exe tuning\\eval_translation.py                 全部影片，目前設定
  .venv\\Scripts\\python.exe tuning\\eval_translation.py --set translate.models.ja=qwen3:8b --note "日文改用 qwen"

指標：chrF（字元 n-gram 的 F 分數，0～100，越高越好）。人工字幕是精簡、意譯的字幕組風格，
所以分數本身偏低；用途是「同一批影片、不同設定」之間的比較。
報告：tuning/reports/translation-時間.md（每句依時間對照人工字幕與我們的譯文）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

from run_cases import recognize, translate  # noqa: E402

from live_translator.asr.streamer import build_engine  # noqa: E402
from live_translator.audio.file_source import load_audio  # noqa: E402
from live_translator.config import glossary_hotwords, load_config, load_corrections, load_glossary  # noqa: E402
from live_translator.translate import build_translator  # noqa: E402
from live_translator.translate.postprocess import to_traditional  # noqa: E402

BENCH = HERE / "benchmark" / "mae"
HELDOUT = HERE / "benchmark" / "heldout"   # 對照組：沒調校過的主播，只驗證不調校
_DROP = re.compile(r"[\s，。！？、…～~「」『』《》（）()!?,.\-—:：;；\"'“”‘’]")


def norm(t: str) -> str:
    t = to_traditional(t, "s2t").replace("臺", "台")
    return _DROP.sub("", t).lower()


def chrf(hyp: str, ref: str, n: int = 4, beta: float = 2.0) -> float:
    """字元 n-gram（1～n）的平均精確率與召回率，再取 F-beta。"""
    hyp, ref = norm(hyp), norm(ref)
    ps, rs = [], []
    for k in range(1, n + 1):
        h = Counter(hyp[i:i + k] for i in range(len(hyp) - k + 1))
        r = Counter(ref[i:i + k] for i in range(len(ref) - k + 1))
        if not h or not r:
            continue
        overlap = sum((h & r).values())
        ps.append(overlap / sum(h.values()))
        rs.append(overlap / sum(r.values()))
    if not ps:
        return 0.0
    p, r = sum(ps) / len(ps), sum(rs) / len(rs)
    if p + r == 0:
        return 0.0
    return 100 * (1 + beta ** 2) * p * r / (beta ** 2 * p + r)


def set_value(cfg: dict, dotted: str, value: str) -> None:
    keys = dotted.split(".")
    d = cfg
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    try:
        value = json.loads(value)
    except ValueError:
        pass
    d[keys[-1]] = value


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--group", choices=["all", "mae", "heldout"], default="all",
                    help="mae＝調校組（鈐 Mae 4 部）、heldout＝對照組（沒調校過的主播）")
    ap.add_argument("--set", action="append", default=[], help="例：translate.models.ja=qwen3:8b")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    cfg = load_config()
    for kv in args.set:
        k, _, v = kv.partition("=")
        set_value(cfg, k, v)
    gpath = cfg.translate.get("glossary")
    glossary, corrections = load_glossary(gpath), load_corrections(gpath)
    engine = build_engine(cfg.asr)
    engine.add_hotwords("ja", glossary_hotwords(cfg.asr, glossary))
    translator = build_translator(cfg.translate, glossary)

    items = []
    if args.group in ("all", "mae"):
        items += sorted(BENCH.glob("*.ref.json"))
    if args.group in ("all", "heldout"):
        items += sorted(HELDOUT.glob("*.ref.json"))
    if args.ids:
        items = [p for p in items if p.name.split(".")[0] in args.ids]
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    lines = [f"# 翻譯基準報告 {stamp}", "", f"- 設定變更：{args.set or '（目前設定）'}"]
    if args.note:
        lines.append(f"- 這次的修改：{args.note}")
    rows, details = [], []
    for p in items:
        vid = p.name.split(".")[0]
        refs = json.loads(p.read_text(encoding="utf-8"))
        audio = load_audio(str(p.with_name(f"{vid}.pcm")))
        finals = recognize(audio, cfg, engine, corrections)
        # 只比較人工字幕有涵蓋的時段（影片裡唱歌、沒上字幕的段落不算）
        finals = [e for e in finals if any(e.start < r["end"] + 1.5 and e.end > r["start"] - 1.5 for r in refs)]
        pairs = translate(finals, translator, cfg)
        ours = "".join(t for _, t in pairs)
        score = chrf(ours, "".join(r["text"] for r in refs))
        rows.append((vid, score, len(refs), len(pairs), p.parent.name))
        print(f"{vid} chrF {score:.1f}", flush=True)
        details += [f"## {vid}　chrF {score:.1f}", "", "| 時間 | 人工字幕 | 我們的譯文 | 我們聽到的原文 |", "|---|---|---|---|"]
        for r in refs:  # 依時間把我們的句子對到人工字幕
            mine = [(e, t) for e, t in pairs if e.start < r["end"] and e.end > r["start"]]
            details.append(f"| {r['start']:6.1f} | {r['text']} | {' '.join(t for _, t in mine)} | "
                           f"{' '.join(e.source for e, _ in mine)} |".replace("\n", " "))
        details.append("")
    def mean(g):
        xs = [s for _, s, _, _, grp in rows if grp == g]
        return sum(xs) / len(xs) if xs else None
    avg, held = mean("mae"), mean("heldout")
    summary = "　".join(x for x in [f"調校組（mae）{avg:.1f}" if avg is not None else "",
                                    f"對照組（heldout）{held:.1f}" if held is not None else ""] if x)
    lines += ["", f"**平均 chrF：{summary}**", "", "| 影片 | 組別 | chrF | 人工字幕句數 | 我們的句數 |", "|---|---|---|---|---|"]
    lines += [f"| {v} | {g} | {s:.1f} | {n} | {m} |" for v, s, n, m, g in rows]
    out = HERE / "reports" / f"translation-{stamp}.md"
    out.write_text("\n".join(lines + [""] + details), encoding="utf-8")
    print(f"\n平均 chrF {summary}；報告：{out}")
    translator.unload()


if __name__ == "__main__":
    main()
