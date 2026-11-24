"""執行回歸測試案例（tuning/cases/cases.yaml）：每段錄音跑完整流程（辨識→修正清單→重新斷句→去口頭禪→翻譯），
檢查必須／不能出現的字，並把每句原文與譯文寫成報告（tuning/reports/）。

  .venv\\Scripts\\python.exe tuning\\run_cases.py            全部案例
  .venv\\Scripts\\python.exe tuning\\run_cases.py C03 C04    只跑指定案例
  .venv\\Scripts\\python.exe tuning\\run_cases.py --note "改了XX"   報告裡記下這次改了什麼

結束代碼：有「退步」（不在 known_fail 的檢查失敗）時為 1。
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from live_translator.asr.streamer import StreamingRecognizer, build_engine  # noqa: E402
from live_translator.audio.file_source import load_audio  # noqa: E402
from live_translator.config import apply_corrections, glossary_hotwords, load_config, load_corrections, load_glossary  # noqa: E402
from live_translator.events import SubtitleEvent  # noqa: E402
from live_translator.segmenter import Segmenter  # noqa: E402
from live_translator.translate import build_translator  # noqa: E402
from live_translator.translate.worker import TranslationWorker  # noqa: E402

SR = 16000


def recognize(audio: np.ndarray, cfg, engine, corrections) -> list[SubtitleEvent]:
    """跑辨識＋修正清單＋重新斷句，回傳依時間排序的定稿句子。"""
    finals: dict[int, SubtitleEvent] = {}

    def deliver(ev: SubtitleEvent) -> None:
        if ev.state == "final":
            finals[ev.id] = ev
        elif ev.state in ("retract", "drop"):
            finals.pop(ev.id, None)

    seg = Segmenter(deliver, enabled=bool(cfg.asr.get("resegment", True)),
                    split=bool(cfg.asr.get("resegment_split", True)))

    def route(ev: SubtitleEvent) -> None:
        if corrections and ev.state in ("partial", "final") and ev.source:
            ev.source = apply_corrections(ev.source, corrections)
        seg.feed(ev)

    q: "queue.Queue[np.ndarray]" = queue.Queue()
    r = StreamingRecognizer(cfg.asr, q, route, engine)
    r.always_sentence_check = True   # 模擬即時播放，結果不受電腦負載影響
    r.start()
    for i in range(0, audio.size, SR // 10):
        q.put(audio[i:i + SR // 10])
        while q.qsize() > 20:
            time.sleep(0.01)
    q.put(np.zeros(SR * 2, np.float32))
    while not q.empty():
        time.sleep(0.1)
    time.sleep(3)
    r.stop()
    seg.flush()
    return sorted(finals.values(), key=lambda e: (e.start, e.id))


def translate(finals: list[SubtitleEvent], translator, cfg) -> list[tuple[SubtitleEvent, str]]:
    w = TranslationWorker(translator, lambda e: None, int(cfg.translate.get("context_sentences", 4)),
                         memory_size=int(cfg.translate.get("memory_size", 0)))
    out = []
    for ev in finals:
        res = w._translate([ev])
        if res and res[0].state == "translated":
            out.append((ev, res[0].target))
        else:
            out.append((ev, ""))  # 整句都是口頭禪，不翻
    return out


def check(case: dict, asr: str, tgt: str) -> list[tuple[str, bool]]:
    results = []
    for w in case.get("asr_contains", []) or []:
        results.append((f"asr_contains:{w}", any(x in asr for x in str(w).split("|"))))
    for w in case.get("asr_not_contains", []) or []:
        results.append((f"asr_not_contains:{w}", w not in asr))
    for w in case.get("tgt_contains", []) or []:
        results.append((f"tgt_contains:{w}", any(x in tgt for x in str(w).split("|"))))
    for w in case.get("tgt_not_contains", []) or []:
        results.append((f"tgt_not_contains:{w}", w not in tgt))
    for k in case.get("known_fail", []) or []:
        kind, _, w = k.partition(":")
        ok = (w in asr) if kind == "asr_contains" else (w in tgt) if kind == "tgt_contains" else False
        results.append((k, ok))
    return results


def set_value(cfg: dict, dotted: str, value: str) -> None:
    """臨時改設定（--set a.b=值）；值能當 JSON 解析就用解析結果（數字、true/false）。"""
    import json

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
    ap.add_argument("--note", default="", help="這次改了什麼（寫進報告）")
    ap.add_argument("--set", action="append", default=[], help="臨時改設定，例：translate.model=qwen3:8b")
    args = ap.parse_args()

    cases = yaml.safe_load((HERE / "cases" / "cases.yaml").read_text(encoding="utf-8"))
    if args.ids:
        cases = [c for c in cases if c["id"] in args.ids]
    cfg = load_config()
    for kv in args.set:
        k, _, v = kv.partition("=")
        set_value(cfg, k, v)
    gpath = cfg.translate.get("glossary")
    glossary, corrections = load_glossary(gpath), load_corrections(gpath)
    engine = build_engine(cfg.asr)
    engine.add_hotwords("ja", glossary_hotwords(cfg.asr, glossary))
    translator = build_translator(cfg.translate, glossary)

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    lines = [f"# 回歸測試報告 {stamp}", "",
             f"- 辨識：{cfg.asr.get('engine', 'whisper')} / {cfg.asr['model']}",
             f"- 翻譯：{cfg.translate['model']}，依語言：{dict(cfg.translate.get('models') or {})}"]
    if args.note:
        lines.append(f"- 這次的修改：{args.note}")
    lines.append("")
    summary, regressions, improved = [], 0, []
    for case in cases:
        t0 = time.perf_counter()
        audio = load_audio(str(HERE / "cases" / case["audio"]))
        finals = recognize(audio, cfg, engine, corrections)
        pairs = translate(finals, translator, cfg)
        asr = "".join(e.source for e, _ in pairs)
        tgt = "".join(t for _, t in pairs)
        known = set(case.get("known_fail", []) or [])
        results = check(case, asr, tgt)
        fails = [k for k, ok in results if not ok and k not in known]
        newly_ok = [k for k, ok in results if ok and k in known]
        regressions += len(fails)
        improved += [f"{case['id']} {k}" for k in newly_ok]
        status = "✅ 通過" if not fails else f"❌ 退步 {len(fails)} 項"
        summary.append(f"| {case['id']} | {case['title']} | {status} | {time.perf_counter() - t0:.0f}s |")
        print(f"{case['id']} {status}  {', '.join(fails)}", flush=True)
        lines += [f"## {case['id']} {case['title']}", "", f"結果：{status}", ""]
        for k, ok in results:
            mark = "✅" if ok else ("⚪ 已知做不到" if k in known else "❌")
            lines.append(f"- {mark} `{k}`")
        lines += ["", "| 時間 | 原文 | 譯文 |", "|---|---|---|"]
        for e, t in pairs:
            lines.append(f"| {e.start:5.1f}s | {e.source.replace('|', '｜')} | {t.replace('|', '｜') or '（口頭禪，不翻）'} |")
        lines.append("")
    head = ["| 案例 | 內容 | 結果 | 耗時 |", "|---|---|---|---|"] + summary
    if improved:
        head += ["", "已知做不到的項目這次通過了（可以考慮把它改成正式檢查）：" + "、".join(improved)]
    lines[6:6] = head + [""]
    out = HERE / "reports" / f"{stamp}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n退步 {regressions} 項；報告：{out}")
    translator.unload()
    sys.exit(1 if regressions else 0)


if __name__ == "__main__":
    main()
