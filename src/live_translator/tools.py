"""check / bench / subs 子命令。"""
from __future__ import annotations

import time
from pathlib import Path

from .config import load_config, load_glossary

BENCH_LINES = [
    "みんなこんばんは！今日も配信来てくれてありがとう！",
    "えっと、今日はね、ずっと楽しみにしてたホラーゲームをやっていこうと思います。",
    "いやいやいや、無理無理無理、絶対なんか出てくるって。",
    "スパチャありがとうございます！めっちゃ嬉しい！",
    "草、なんでそこで死ぬの？",
    "あの、次の配信はメン限でやるかもしれないです。",
    "ちょっと待って、今の見た？やばくない？",
    "じゃあそろそろ終わりにしようかな、おつかれさまでした！",
]


def cmd_check(args) -> None:
    import subprocess

    cfg = load_config(args.config)
    ok = True

    print("== GPU ==")
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.used,memory.total",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
        print(out.stdout.strip())
    except Exception as e:
        ok = False
        print(f"  nvidia-smi 失敗：{e}")

    print("\n== CTranslate2 / Whisper ==")
    try:
        from .gpu import setup_cuda_dlls

        setup_cuda_dlls()
        import ctranslate2

        print(f"ctranslate2 {ctranslate2.__version__}，CUDA 裝置數：{ctranslate2.get_cuda_device_count()}")
        print(f"支援的運算型別：{sorted(ctranslate2.get_supported_compute_types('cuda'))}")
        import numpy as np

        from .asr.streamer import WhisperEngine

        eng = WhisperEngine(cfg.asr)
        audio = np.zeros(16000 * 3, np.float32)
        t0 = time.perf_counter()
        eng.transcribe(audio, final=True)
        print(f"3 秒靜音辨識耗時 {time.perf_counter() - t0:.2f}s（第一次含暖機）")
        t0 = time.perf_counter()
        eng.transcribe(np.random.randn(16000 * 10).astype(np.float32) * 0.01, final=True)
        print(f"10 秒音訊辨識耗時 {time.perf_counter() - t0:.2f}s")
    except Exception as e:
        ok = False
        print(f"  Whisper 失敗：{e!r}")

    print("\n== 翻譯 ==")
    try:
        from .translate import build_translator

        tr = build_translator(cfg.translate, load_glossary(cfg.translate.get("glossary")))
        t0 = time.perf_counter()
        tr.translate("こんにちは")
        print(f"暖機 {time.perf_counter() - t0:.2f}s")
        for line in BENCH_LINES[:3]:
            t0 = time.perf_counter()
            out = tr.translate(line)
            print(f"  {time.perf_counter() - t0:5.2f}s  {line}\n         → {out}")
    except Exception as e:
        ok = False
        print(f"  翻譯失敗：{e!r}（Ollama 有在執行嗎？模型 {cfg.translate['model']} 下載了嗎？）")

    print("\n全部正常" if ok else "\n有項目失敗，請看上面訊息")


def cmd_bench(args) -> None:
    from .translate import build_translator

    cfg = load_config(args.config)
    glossary = load_glossary(cfg.translate.get("glossary"))
    models = args.models or [cfg.translate["model"]]
    for m in models:
        tcfg = dict(cfg.translate)
        tcfg["model"] = m
        tr = build_translator(tcfg, glossary)
        print(f"\n===== {m} =====")
        try:
            tr.translate("こんにちは")
        except Exception as e:
            print(f"  失敗：{e!r}")
            continue
        ctx: list[tuple[str, str]] = []
        times = []
        for line in BENCH_LINES:
            t0 = time.perf_counter()
            out = tr.translate(line, ctx[-4:])
            dt = time.perf_counter() - t0
            times.append(dt)
            ctx.append((line, out))
            print(f"  {dt:5.2f}s  {line}\n         → {out}")
        print(f"  平均 {sum(times) / len(times):.2f}s，最慢 {max(times):.2f}s")


def cmd_subs(args) -> None:
    from .subtitles.formats import to_srt
    from .subtitles.youtube import IsLive, NoSubtitles, translate_video, video_id_of
    from .translate import build_translator

    cfg = load_config(args.config)
    tr = build_translator(cfg.translate, load_glossary(cfg.translate.get("glossary")))
    vid = video_id_of(args.url)
    t0 = time.perf_counter()

    def progress(done, total, _chunk):
        print(f"\r[subs] 翻譯中 {done}/{total}", end="", flush=True)

    try:
        cache_key = cfg.translate["model"] + "".join(f"+{k}={v}" for k, v in (cfg.translate.get("models") or {}).items())
        cues = translate_video(vid, tr, cache_key, on_progress=progress,
                               use_cache=not args.no_cache)
    except IsLive:
        print("這是直播中的影片，沒有字幕檔可抓，請用即時聽譯模式。")
        return
    except NoSubtitles:
        print("這部影片沒有日文字幕（官方或自動），請用即時聽譯模式。")
        return
    out = Path(args.output or f"{vid}.zh-TW.srt")
    out.write_text(to_srt(cues, bilingual=not args.mono), encoding="utf-8")
    print(f"\n[subs] 完成 {len(cues)} 句，{time.perf_counter() - t0:.1f}s → {out}")
