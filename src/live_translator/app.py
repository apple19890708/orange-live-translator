"""即時聽譯主程式：擷取聲音 → 辨識 → 翻譯 → 浮動視窗／WebSocket／SRT。"""
from __future__ import annotations

import signal
import threading
import time

from .config import apply_corrections, glossary_hotwords, load_config, load_corrections, load_glossary
from .events import EventBus, SubtitleEvent


class _LoadingEngine:
    """切換辨識模型時的佔位：載入完成前不輸出任何字。"""

    def __init__(self, lang: str):
        self.current_lang = "ja" if lang == "auto" else lang
        self.language = lang

    def detect(self, audio):
        return self.current_lang

    def transcribe(self, *a, **k):
        return ""

    def set_language(self, lang):
        pass


def open_recording(keep: int, same_name_as=None):
    """把這次聽到的聲音存成 logs/live-時間.pcm（16kHz 單聲道 16 位元），只留最近 keep 個。
    檔名和字幕紀錄 .srt 相同，字幕時間就是音檔裡的秒數，方便回放比對聽錯的地方。"""
    from datetime import datetime

    from .config import ROOT

    d = ROOT / "logs"
    d.mkdir(exist_ok=True)
    old = sorted(d.glob("live-*.pcm"))
    for p in old[: max(0, len(old) - keep + 1)]:
        try:
            p.unlink()
        except OSError:
            pass
    path = (same_name_as.with_suffix(".pcm") if same_name_as
            else d / f"live-{datetime.now():%Y%m%d-%H%M%S}.pcm")
    print(f"[log] 錄音：{path}")
    return open(path, "ab", buffering=0)


def run_live(config_path: str | None = None, file: str | None = None, no_overlay: bool = False,
             speed: float = 1.0, source_kind: str | None = None) -> None:
    cfg = load_config(config_path)
    from .user_state import load_state

    if load_state().get("asr_engine"):
        cfg["asr"]["engine"] = load_state()["asr_engine"]
    no_overlay = no_overlay or not cfg.overlay.get("enabled", True)
    glossary = load_glossary(cfg.translate.get("glossary"))
    corrections = load_corrections(cfg.translate.get("glossary"))
    bus = EventBus()

    # ---- 翻譯 ----
    from .translate import build_translator
    from .translate.worker import TranslationWorker

    translator = build_translator(cfg.translate, glossary)
    models = dict(cfg.translate.get("models") or {})
    short = lambda m: m.split("/")[-1].split(":")[0] if "/" in m else m  # noqa: E731
    model_label = " / ".join([f"{k}:{short(v)}" for k, v in models.items()] + [f"其他:{short(cfg.translate['model'])}"])         if models else cfg.translate["model"]
    worker = TranslationWorker(translator, bus.publish, int(cfg.translate.get("context_sentences", 4)),
                               memory_size=int(cfg.translate.get("memory_size", 0)))

    # 記下用到的 Ollama 模型：OrangeLiveTranslate.exe 強制結束程式後，靠這份清單把模型移出顯示記憶體
    import json

    from .config import ROOT

    models_file = ROOT / "logs" / "models.json"
    models_file.parent.mkdir(exist_ok=True)
    models_file.write_text(json.dumps({
        "base_url": cfg.translate.get("base_url") or "http://127.0.0.1:11434",
        "models": translator.all_models() if (cfg.translate.get("provider") or "ollama") == "ollama" else [],
    }), encoding="utf-8")

    def deliver(ev: SubtitleEvent) -> None:
        bus.publish(ev)
        if ev.state == "final":
            worker.submit(ev)
        elif ev.state == "retract":
            worker.retract(ev)

    # 辨識結果依句號重新斷句（一段多句拆開、結尾碎片接到下一句）
    from .segmenter import Segmenter

    segmenter = Segmenter(deliver, enabled=bool(cfg.asr.get("resegment", True)),
                          split=bool(cfg.asr.get("resegment_split", True)))

    replay = None  # 依影片時間快取字幕（只有分頁聽譯才有影片時間，下面建立聲音來源後才決定）

    def route(ev: SubtitleEvent) -> None:
        if replay is not None and ev.state in ("partial", "final") and not replay.allow(ev):
            # 這段影片已經翻過（往回拉時間軸重看）：不重翻，改由快取字幕顯示
            if ev.state == "final":
                segmenter.feed(SubtitleEvent(ev.id, "drop", ""))
            return
        if corrections and ev.state in ("partial", "final") and ev.source:
            ev.source = apply_corrections(ev.source, corrections)
        segmenter.feed(ev)

    # ---- 聲音來源 ----
    source_kind = "file" if file else (source_kind or cfg.audio.get("source", "tab"))
    tab_source = None
    if file:
        from .audio.file_source import FileSource

        source = FileSource(file, speed=speed)
    elif source_kind == "tab":
        from .audio.tab_source import TabAudioSource

        source = tab_source = TabAudioSource()
    else:
        from .audio.capture import LoopbackCapture

        source = LoopbackCapture(cfg.audio.get("device", ""))

    replay_cfg = cfg.get("replay") or {}
    if tab_source is not None and replay_cfg.get("enabled", True):
        from .replay import MediaReplay

        replay = MediaReplay(min_cover=float(replay_cfg.get("min_cover", 0.5)))
        bus.subscribe(replay.on_event)

    def on_media_time(msg: dict) -> None:
        if replay is None:
            return
        try:
            replay.anchor(tab_source.received_s, str(msg.get("video_id") or "")[:20], float(msg.get("t") or 0),
                          paused=bool(msg.get("paused")), rate=float(msg.get("rate") or 1.0),
                          ad=bool(msg.get("ad")), seek=bool(msg.get("seek")))
        except (TypeError, ValueError):
            pass

    def replay_tick(stream_t: float) -> None:
        """辨識執行緒每處理一段聲音呼叫一次：播到已翻過的句子就送出當時的字幕。"""
        r = recognizer_holder.get("r")
        for cue in replay.due(stream_t):
            eid = r.alloc_id()
            eid = eid * 100 if segmenter.enabled else eid  # 和重新斷句後的 id 同一套順序
            replay.mark_replayed(eid)
            bus.publish(SubtitleEvent(eid, "translated", cue.source, cue.target,
                                      stream_t, stream_t + (cue.mend - cue.mstart), cue.lang))

    state = {"live": False, "capturing": False, "tab_title": "",
             "language": cfg.asr.get("language", "auto")}
    notify_holder: dict = {"fn": lambda s: None}

    def status() -> dict:
        from .asr.streamer import ENGINES

        eng = cfg.asr.get("engine", "whisper")
        return {"live": state["live"], "asr_model": cfg.asr["model"] if eng == "whisper" else ENGINES.get(eng, eng),
                "translate_model": model_label, "source": source_kind,
                "capturing": state["capturing"], "tab_title": state["tab_title"],
                "language": state["language"]}

    def on_capture(on: bool, title: str) -> None:
        state["capturing"], state["tab_title"] = on, (title if on else "")
        print(f"[audio] {'開始聽譯分頁：' + title if on else '停止聽譯分頁'}")
        if state["live"]:
            notify_holder["fn"]("" if on else "等待在 Chrome 擴充功能按「聽譯這個分頁」")

    overlay_holder: dict = {}

    def make_engine(kind: str):
        from .asr.streamer import build_engine

        engine = build_engine({**cfg.asr, "engine": kind})
        engine.set_language(state["language"])
        # 詞彙表裡的日文詞也當作辨識提示，人名、專有名詞比較不會聽錯
        engine.add_hotwords("ja", glossary_hotwords(cfg.asr, glossary))
        return engine

    engine_lock = threading.Lock()

    def on_engine(kind: str) -> None:
        """執行中切換辨識模型：先載入新的，換上去，再釋放舊的。"""
        from .asr.streamer import ENGINES
        from .user_state import save_state

        def work():
            if not engine_lock.acquire(blocking=False):
                return
            try:
                r = recognizer_holder.get("r")
                notify_holder["fn"](f"載入 {ENGINES.get(kind, kind)}…（第一次會先下載）")
                old = r.engine if r else None
                if r:
                    r.engine = _LoadingEngine(state["language"])  # 載入期間先不辨識
                if old is not None and hasattr(old, "close"):
                    old.close()  # 先釋放，避免兩個模型同時佔顯示記憶體
                new = make_engine(kind)
                if r:
                    r.engine = new
                cfg["asr"]["engine"] = kind
                save_state(asr_engine=kind)
                print(f"[asr] 辨識模型：{ENGINES.get(kind, kind)}")
                notify_holder["fn"]("")
                win = overlay_holder.get("win")
                if win:
                    win.set_engine(kind)
                ws.broadcast({"type": "status", **status()})
            except Exception as e:
                import traceback

                traceback.print_exc()
                notify_holder["fn"](f"切換失敗：{e}")
            finally:
                engine_lock.release()

        threading.Thread(target=work, daemon=True).start()

    def on_language(lang: str) -> None:
        state["language"] = lang
        r = recognizer_holder.get("r")
        if r:
            r.engine.set_language(lang)
        print(f"[asr] 辨識語言：{lang}")
        win = overlay_holder.get("win")
        if win:
            win.set_language(lang)
        ws.broadcast({"type": "status", **status()})

    # ---- WebSocket ----
    from .server.ws import WSServer

    ws = WSServer(cfg.server["host"], int(cfg.server["port"]), status, translator, model_label,
                  audio_sink=tab_source.push_pcm16 if tab_source else None,
                  on_capture=on_capture if tab_source else None, on_language=on_language,
                  on_shutdown=lambda: request_shutdown(), on_engine=lambda k: on_engine(k),
                  on_media_time=on_media_time if replay is not None else None)
    ws.start()
    bus.subscribe(ws.publish)

    srt_holder: dict = {}
    if cfg.log.get("save_srt", True):
        from .srt_log import SrtLogger

        srt = SrtLogger()
        srt_holder["path"] = srt.path
        srt_holder["logger"] = srt
        bus.subscribe(srt.on_event)
        print(f"[log] 字幕紀錄：{srt.path}")

    recognizer_holder: dict = {}
    stop_event = threading.Event()

    def request_shutdown() -> None:
        """OrangeLiveTranslate.exe 要求關閉：走正常流程（寫完字幕紀錄、釋放模型）。"""
        print("[app] 收到關閉要求")
        win = overlay_holder.get("win")
        if win is not None:
            win.request_quit()
        else:
            stop_event.set()

    def set_paused(on: bool) -> None:
        r = recognizer_holder.get("r")
        if r:
            (r.paused.set if on else r.paused.clear)()

    def boot(notify) -> None:
        """在背景載入模型，避免視窗卡住。"""
        from .asr.streamer import StreamingRecognizer, build_engine

        notify_holder["fn"] = notify
        try:
            notify("載入翻譯模型…")
            translator.warmup()
            notify("載入語音辨識模型…（第一次會下載約 1.6GB）")
            engine = make_engine(cfg.asr.get("engine", "whisper"))
            r = StreamingRecognizer(cfg.asr, source.queue, route, engine)
            if cfg.log.get("save_audio", True) and not file:
                r.recorder = open_recording(int(cfg.log.get("keep_audio", 3)), srt_holder.get("path"))
            recognizer_holder["r"] = r
            if replay is not None:
                r.on_tick = replay_tick
            worker.start()
            r.start()
            source.start()
            state["live"] = True
            ws.broadcast({"type": "status", **status()})
            if tab_source and not state["capturing"]:
                notify("等待在 Chrome 擴充功能按「聽譯這個分頁」")
            else:
                notify("")
            print(f"[app] 模型就緒，聲音來源：{source.device_name}")
        except Exception as e:
            import traceback

            traceback.print_exc()
            notify(f"啟動失敗：{e}")
            if no_overlay:
                stop_event.set()

    def shutdown() -> None:
        stop_event.set()
        try:
            source.stop()
        except Exception:
            pass
        r = recognizer_holder.get("r")
        if r:
            r.stop()
        worker.stop()
        # 翻譯模型由 Ollama 常駐；程式關閉就請它立刻釋放顯示記憶體
        segmenter.flush()
        if srt_holder.get("logger"):
            srt_holder["logger"].flush()
        translator.unload()
        if r and r.recorder is not None:
            try:
                r.recorder.close()
            except Exception:
                pass
        try:
            models_file.unlink()
        except OSError:
            pass

    if no_overlay:
        def console(ev: SubtitleEvent) -> None:
            r = recognizer_holder.get("r")
            # 延遲 = 現在已播到的時間 − 這句結束的時間（含停頓判定、辨識、翻譯）
            lag = (r.stream_time - ev.end) if r else 0.0
            if ev.state == "partial":
                print(f"\r  … {ev.source[-60:]}", end="", flush=True)
            elif ev.state == "final":
                tag = {"ja": "日", "en": "英"}.get(ev.lang, ev.lang or "原")
                print(f"\r\033[K[{ev.start:7.1f}s] {tag}：{ev.source}  (+{lag:.1f}s)")
            elif ev.state == "translated":
                print(f"\r\033[K           中：{ev.target}  (+{lag:.1f}s)")

        bus.subscribe(console)
        signal.signal(signal.SIGINT, lambda *_: stop_event.set())
        boot(lambda s: s and print(f"[app] {s}"))
        try:
            while not stop_event.is_set():
                if file and getattr(source, "done", None) and source.done.is_set() \
                        and source.queue.empty() and worker.q.empty():
                    time.sleep(3)  # 等最後一句翻完
                    break
                time.sleep(0.2)
        finally:
            shutdown()
        return

    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    from .overlay.window import OverlayWindow

    win = OverlayWindow(cfg.overlay, on_pause=set_paused, on_quit=shutdown,
                        on_language=on_language, language=state["language"],
                        on_engine=on_engine, engine=cfg.asr.get("engine", "whisper"))
    overlay_holder["win"] = win
    bus.subscribe(win.push)
    win.show()
    threading.Thread(target=boot, args=(win.set_status,), daemon=True).start()
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # 讓 Ctrl+C 在 Qt 事件迴圈中也能生效
    from PySide6.QtCore import QTimer

    t = QTimer()
    t.start(300)
    t.timeout.connect(lambda: None)
    app.exec()
    shutdown()
