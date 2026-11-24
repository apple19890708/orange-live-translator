"""用參考逐字稿評分即時辨識：跑完整即時流程（VAD 切句等），算字元錯誤率 CER。

  .venv\\Scripts\\python.exe scripts\\eval_asr.py cache\\test\\utano_announce.wav tests\\data\\utano_announce_ref.txt \\
      --set prompt_chars=60 --set prompt_chars=150 ...

每個 --set 是一組設定（逗號分隔多個 key=value），會各跑一次；不給 --set 就只跑目前設定。
"""
from __future__ import annotations

import argparse
import queue
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402

from live_translator.asr.streamer import StreamingRecognizer, build_engine  # noqa: E402
from live_translator.audio.file_source import load_audio  # noqa: E402
from live_translator.config import load_config, load_glossary  # noqa: E402

SR = 16000
_FILLERS = re.compile(r"(えーっと|えーと|えっと|えー|あのー|あの|ええ|うーん|んー|はーい|はい)")
_DROP = re.compile(r"[\s、。！？!?,.・「」『』…ー〜~\-]")


def normalize(t: str) -> str:
    return _DROP.sub("", _FILLERS.sub("", t))


def cer(hyp: str, ref: str) -> float:
    h, r = normalize(hyp), normalize(ref)
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return prev[-1] / max(1, len(r))


def cer_ops(hyp: str, ref: str) -> tuple[int, int, int, int]:
    """拆開錯誤種類：回傳（錯字 S、漏字 D、多字 I、答案字數 N）。

    答案不是逐字稿時（例：剪輯者的字幕會跳過一些話），「多字」大多是真的有說、答案沒收的話，
    這時看「漏聽＋聽錯率」(S+D)/N 才準；多字另外看，用來抓幻聽。
    """
    h, r = normalize(hyp), normalize(ref)
    prev = [(j, 0, 0, j) for j in range(len(h) + 1)]       # (成本, S, D, I)
    for i, rc in enumerate(r, 1):
        cur = [(i, 0, i, 0)] + [None] * len(h)
        for j, hc in enumerate(h, 1):
            d, s_, ins = prev[j - 1], prev[j], cur[j - 1]
            sub = (d[0] + (rc != hc), d[1] + (rc != hc), d[2], d[3])
            dele = (s_[0] + 1, s_[1], s_[2] + 1, s_[3])
            ins_ = (ins[0] + 1, ins[1], ins[2], ins[3] + 1)
            cur[j] = min(sub, dele, ins_, key=lambda x: x[0])
        prev = cur
    _, s, d, i = prev[-1]
    return s, d, i, len(r)


def split_settings(v: str) -> list[str]:
    """用逗號分隔多個 key=value，但中括號裡的逗號不算（例：temperatures=[0.0,0.2]）。"""
    out, depth, cur = [], 0, ""
    for ch in v:
        depth += ch == "["
        depth -= ch == "]"
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [x for x in out + [cur] if x.strip()]


def parse_value(v: str):
    if v.startswith("["):
        import json

        return json.loads(v)
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    if v in ("{}", "none", "None"):
        return {}
    return {"true": True, "false": False}.get(v.lower(), v)


def run(audio: np.ndarray, acfg: dict, glossary: dict, engine_cache: dict) -> tuple[str, list[str], float]:
    key = (acfg.get("engine", "whisper"), acfg["model"], acfg.get("compute_type"))
    if key not in engine_cache:
        engine_cache.clear()
        engine_cache[key] = build_engine(acfg)
    eng = engine_cache[key]
    eng.cfg = acfg
    eng.language = "ja"
    eng.current_lang = "ja"
    eng.hotwords = {k: " ".join(v) for k, v in (acfg.get("hotwords") or {}).items() if v}
    if acfg.get("glossary_hotwords", True):
        eng.add_hotwords("ja", [k for k in glossary if any(ord(c) > 0x3000 for c in k)])
    q: "queue.Queue[np.ndarray]" = queue.Queue()
    finals_by_id: dict[int, str] = {}
    lags: list[float] = []

    def on_event(ev):
        if ev.state == "final":
            finals_by_id[ev.id] = ev.source
            # 延遲：這句結束後，又聽了幾秒的聲音才定稿（不含辨識計算時間）
            lags.append(r.stream_time - ev.end)
        elif ev.state == "retract":  # 被合併到下一句的句子不算
            finals_by_id.pop(ev.id, None)

    r = StreamingRecognizer(acfg, q, on_event, eng)
    r.always_sentence_check = True   # 模擬即時播放，結果不受電腦負載影響
    t0 = time.perf_counter()
    r.start()
    block = SR // 10
    for i in range(0, audio.size, block):
        q.put(audio[i:i + block])
        while q.qsize() > 20:  # 不要一次塞太多，模擬即時
            time.sleep(0.01)
    q.put(np.zeros(SR * 2, np.float32))
    while not q.empty():
        time.sleep(0.1)
    time.sleep(3)
    r.stop()
    finals = [finals_by_id[k] for k in sorted(finals_by_id)]
    lags.sort()
    if lags:
        print(f"   （句號切 {getattr(r, 'sentence_cuts', 0)}/{getattr(r, 'sentence_checks', 0)}；合併重聽 {getattr(r, 'merged_count', 0)} 次；定稿延遲 中位數 {lags[len(lags) // 2]:.1f}s、"
              f"90% {lags[int(len(lags) * 0.9)]:.1f}s、最慢 {lags[-1]:.1f}s）")
    return "".join(finals), finals, time.perf_counter() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("ref")
    ap.add_argument("--set", action="append", default=[], help="例：prompt_chars=60,beam=5")
    ap.add_argument("--show", action="store_true", help="印出每句辨識結果")
    args = ap.parse_args()

    cfg = load_config()
    glossary = load_glossary(cfg.translate.get("glossary"))
    audio = load_audio(args.audio)
    ref = Path(args.ref).read_text(encoding="utf-8")
    variants = args.set or [""]
    cache: dict = {}
    print(f"參考稿 {len(normalize(ref))} 字（去掉標點和語助詞）")
    for v in variants:
        acfg = dict(cfg.asr)
        for kv in split_settings(v):
            k, _, val = kv.partition("=")
            acfg[k.strip()] = parse_value(val.strip())
        hyp, finals, secs = run(audio, acfg, glossary, cache)
        print(f"[{v or '目前設定'}] CER {cer(hyp, ref) * 100:.1f}%  句數 {len(finals)}  耗時 {secs:.0f}s", flush=True)
        if args.show:
            for f in finals:
                print("   ", f)


if __name__ == "__main__":
    main()
