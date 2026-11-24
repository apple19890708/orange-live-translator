"""實驗：翻譯前先用 LLM 修正辨識錯字（參考 jt-live-whisper 的 ASR 校正），用參考稿量 CER 前後差異。

  .venv\\Scripts\\python.exe scripts\\eval_correction.py 辨識結果.txt 參考稿.txt [--model qwen3:8b]

辨識結果.txt：一行一句（例如 eval_asr.py --show 的輸出）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_asr import cer  # noqa: E402
from live_translator.config import load_config, load_glossary  # noqa: E402
from live_translator.translate.base import relevant_glossary  # noqa: E402
from live_translator.translate.ollama import OllamaTranslator  # noqa: E402

# jt-live-whisper 的經驗：用中文寫提示詞，模型容易直接翻譯；改用英文
SYSTEM = """You correct Japanese speech-recognition output from a live stream.
Fix only clear recognition errors: homophones, wrong kanji, misheard proper nouns or terms.
Do NOT translate, do NOT rephrase, do NOT add or remove content, keep fillers and numbers as they are.
If nothing is clearly wrong, return the line unchanged. Output only the corrected Japanese line."""


def build_user(line: str, context: list[str], glossary: dict[str, str]) -> str:
    parts = []
    g = relevant_glossary(glossary, [line + "".join(context)]) or {}
    terms = list(glossary)[:60]
    parts.append("Known terms that may appear: " + "、".join(terms))
    if context:
        parts.append("Previous lines (already correct):\n" + "\n".join(context))
    parts.append("Line to correct:\n" + line)
    return "\n\n".join(parts)


def guard(orig: str, fixed: str) -> str:
    """防止過度修改：長度變化太大、出現中文翻譯、或多了括號說明，就保留原句。"""
    fixed = fixed.strip().splitlines()[0].strip() if fixed.strip() else ""
    if not fixed:
        return orig
    if abs(len(fixed) - len(orig)) > max(4, len(orig) * 0.25):
        return orig
    kana = sum(1 for c in fixed if "぀" <= c <= "ヿ")
    if kana == 0 and any("぀" <= c <= "ヿ" for c in orig):
        return orig  # 變成沒有假名＝大概被翻成中文了
    if any(b in fixed for b in "（(【[") and not any(b in orig for b in "（(【["):
        return orig
    return fixed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("hyp")
    ap.add_argument("ref")
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--context", type=int, default=3)
    args = ap.parse_args()

    cfg = load_config()
    glossary = load_glossary(cfg.translate.get("glossary"))
    tcfg = dict(cfg.translate)
    tcfg["model"] = args.model
    tcfg["temperature"] = 0.0
    llm = OllamaTranslator(tcfg, {})
    lines = [ln.strip() for ln in Path(args.hyp).read_text(encoding="utf-8").splitlines() if ln.strip()]
    ref = Path(args.ref).read_text(encoding="utf-8")

    fixed_lines, ctx, changed, times = [], [], 0, []
    for ln in lines:
        t0 = time.perf_counter()
        out = guard(ln, llm.chat(SYSTEM, build_user(ln, ctx[-args.context:], glossary)))
        times.append(time.perf_counter() - t0)
        if out != ln:
            changed += 1
            print(f"  改：{ln}\n  →：{out}")
        fixed_lines.append(out)
        ctx.append(out)
    before, after = cer("".join(lines), ref), cer("".join(fixed_lines), ref)
    print(f"\n修改 {changed}/{len(lines)} 句；CER {before * 100:.1f}% → {after * 100:.1f}%；"
          f"每句平均 {sum(times) / len(times):.2f}s")
    llm.unload()


if __name__ == "__main__":
    main()
