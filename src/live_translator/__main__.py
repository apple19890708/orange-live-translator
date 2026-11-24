"""命令列入口。

  python -m live_translator                 即時聽譯（浮動字幕視窗）
  python -m live_translator --no-overlay    即時聽譯（只在終端機顯示）
  python -m live_translator --file a.mp3    用音檔模擬即時聽譯（測試用）
  python -m live_translator subs <網址>     翻譯 YouTube 字幕並輸出 SRT
  python -m live_translator refine 錄音.pcm  直播後精聽（更準的雙語字幕＋和即時字幕的比對）
  python -m live_translator check           檢查 GPU、Whisper、Ollama
  python -m live_translator bench           比較翻譯模型的速度與品質
  python -m live_translator devices         列出可擷取的音訊裝置
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    import os

    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    ap = argparse.ArgumentParser(prog="live-translator", description="Orange Live Translate：本機 GPU 即時日翻中字幕")
    ap.add_argument("-c", "--config", help="設定檔路徑（預設 config.yaml）")
    sub = ap.add_subparsers(dest="cmd")

    p_live = sub.add_parser("live", help="即時聽譯（預設）")
    for p in (ap, p_live):
        p.add_argument("--no-overlay", action="store_true", help="不開字幕視窗，只在終端機顯示")
        p.add_argument("--file", help="用音檔代替 loopback 擷取（測試用）")
        p.add_argument("--speed", type=float, default=1.0, help="--file 播放速度倍率")
        p.add_argument("--source", choices=["tab", "system"], help="tab=只聽 Chrome 指定分頁，system=整台電腦的聲音（預設看設定檔）")

    p_subs = sub.add_parser("subs", help="翻譯 YouTube 字幕輸出 SRT")
    p_subs.add_argument("url")
    p_subs.add_argument("-o", "--output", help="輸出 SRT 路徑（預設 <影片ID>.zh-TW.srt）")
    p_subs.add_argument("--no-cache", action="store_true")
    p_subs.add_argument("--mono", action="store_true", help="只輸出中文，不附原文")

    p_ref = sub.add_parser("refine", help="直播後精聽：用錄音離線重新辨識，產出更準的雙語字幕與比對報告")
    p_ref.add_argument("audio", help="錄音（logs/live-*.pcm）或任何音檔")
    p_ref.add_argument("--no-translate", action="store_true", help="只辨識不翻譯")

    sub.add_parser("check", help="環境檢查")
    p_bench = sub.add_parser("bench", help="翻譯模型比較")
    p_bench.add_argument("models", nargs="*", help="Ollama 模型名稱，預設用設定檔的模型")
    sub.add_parser("devices", help="列出 loopback 裝置")

    args = ap.parse_args(argv)

    if args.cmd == "subs":
        from .tools import cmd_subs

        cmd_subs(args)
    elif args.cmd == "refine":
        from .refine import run_refine

        run_refine(args.audio, args.config, translate=not args.no_translate)
    elif args.cmd == "check":
        from .tools import cmd_check

        cmd_check(args)
    elif args.cmd == "bench":
        from .tools import cmd_bench

        cmd_bench(args)
    elif args.cmd == "devices":
        from .audio.capture import list_loopback_devices

        for d in list_loopback_devices():
            print(f"[{d['index']}] {d['name']}  {int(d['defaultSampleRate'])}Hz {d['maxInputChannels']}ch")
    else:
        from .app import run_live

        run_live(args.config, file=args.file, no_overlay=args.no_overlay, speed=args.speed,
                 source_kind=args.source)


if __name__ == "__main__":
    main()
