"""並排比較翻譯模型：讀即時聽譯存下的 SRT（取日文原文），每個模型帶前文逐句翻譯，輸出 Markdown 表格。

用法：
  .venv\\Scripts\\python.exe scripts\\compare_models.py logs\\live-xxxx.srt -n 20 -o docs\\translation-compare.md
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from live_translator.config import load_config, load_glossary  # noqa: E402
from live_translator.tools import BENCH_LINES  # noqa: E402
from live_translator.translate.base import _build_single  # noqa: E402

OLLAMA = Path.home() / "AppData/Local/Programs/Ollama/ollama.exe"


def read_sources(srt: Path, n: int) -> list[str]:
    out = []
    for block in re.split(r"\n\s*\n", srt.read_text(encoding="utf-8")):
        lines = [ln for ln in block.strip().splitlines() if ln.strip()]
        if len(lines) >= 4:  # 編號、時間、譯文、原文
            src = lines[3].strip()
            if len(src) >= 4:
                out.append(src)
    return out[:n]


def run_model(model: str, lines: list[str], cfg, glossary) -> tuple[list[str], list[float]]:
    tcfg = dict(cfg.translate)
    tcfg["model"] = model
    tcfg.pop("models", None)
    tr = _build_single(tcfg, glossary)
    tr.warmup()
    ctx: list[tuple[str, str]] = []
    outs, times = [], []
    for ln in lines:
        t0 = time.perf_counter()
        out = tr.translate(ln, ctx[-4:], "ja")
        times.append(time.perf_counter() - t0)
        outs.append(out)
        ctx.append((ln, out))
        print(f"  {times[-1]:4.2f}s {ln[:30]} → {out}", flush=True)
    # 換下一個模型前卸載，公平比較顯示記憶體
    subprocess.run([str(OLLAMA), "stop", model], capture_output=True)
    return outs, times


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("srt", nargs="?")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("-o", "--output", default="docs/translation-compare.md")
    ap.add_argument("--models", nargs="+",
                    default=["qwen3:8b", "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3.7:IQ4_XS"])
    args = ap.parse_args()

    cfg = load_config()
    glossary = load_glossary(cfg.translate.get("glossary"))
    lines = (read_sources(Path(args.srt), args.n) if args.srt else []) + list(BENCH_LINES)

    results = {}
    for m in args.models:
        print(f"===== {m} =====", flush=True)
        results[m] = run_model(m, lines, cfg, glossary)

    short = {m: m.split("/")[-1].split(":")[0] if "/" in m else m for m in args.models}
    md = ["# 翻譯模型比較", "",
          f"同樣 {len(lines)} 句日文、同樣帶前 4 句前文、同樣經 OpenCC 轉台灣用語。"
          "前段是 Whisper 從實際直播片段聽出來的句子，後段是直播主常見說法。", "",
          "| 模型 | 平均秒數 | 最慢 |", "|---|---|---|"]
    for m in args.models:
        t = results[m][1]
        md.append(f"| {short[m]} | {sum(t) / len(t):.2f}s | {max(t):.2f}s |")
    md += ["", "| # | 日文原文 | " + " | ".join(short[m] for m in args.models) + " |",
           "|---|---|" + "---|" * len(args.models)]
    for i, ln in enumerate(lines):
        cells = [results[m][0][i].replace("|", "｜") for m in args.models]
        md.append(f"| {i + 1} | {ln.replace('|', '｜')} | " + " | ".join(cells) + " |")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
