"""本機 WebSocket 伺服器（只聽 127.0.0.1）。

伺服器 → 用戶端：
  {"type":"subtitle", id, state, source, target, start, end}     即時字幕事件
  {"type":"status", live, asr_model, translate_model}
  {"type":"video_cues", video_id, cues:[...], done, total}       影片字幕（分批送）
  {"type":"video_error", video_id, reason, message}              reason: live / no_subs / error
  {"type":"translate_result", req, text}

用戶端 → 伺服器：
  {"type":"load_video", video_id}
  {"type":"translate", req, text}
  {"type":"set_language", lang}                                   auto / ja / en
  {"type":"set_engine", engine}                                   whisper / anime
  {"type":"capture", on, title}                                    開始／停止送分頁聲音
  {"type":"media_time", video_id, t, paused, rate, ad, seek}       聽譯中分頁的影片播放時間（依影片時間快取字幕）
  二進位訊息                                                       分頁聲音：16kHz 單聲道 Int16 PCM
"""
from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from websockets.asyncio.server import ServerConnection, serve

from ..events import SubtitleEvent

ALLOWED_ORIGIN_PREFIXES = ("chrome-extension://", "moz-extension://", "http://127.0.0.1", "http://localhost")


class WSServer:
    def __init__(self, host: str, port: int, status: Callable[[], dict],
                 translator=None, model_name: str = "",
                 audio_sink: Callable[[bytes], None] | None = None,
                 on_capture: Callable[[bool, str], None] | None = None,
                 on_language: Callable[[str], None] | None = None,
                 on_shutdown: Callable[[], None] | None = None,
                 on_engine: Callable[[str], None] | None = None,
                 on_media_time: Callable[[dict], None] | None = None):
        self.host, self.port = host, port
        self.status = status
        self.translator = translator
        self.model_name = model_name
        self.audio_sink = audio_sink      # 分頁聲音（二進位 PCM16）
        self.on_capture = on_capture      # 擴充功能開始／停止聽譯分頁
        self.on_language = on_language    # 切換辨識語言 auto / ja / en
        self.on_shutdown = on_shutdown    # OrangeLiveTranslate.exe 要求正常關閉
        self.on_engine = on_engine        # 切換辨識模型 whisper / anime
        self.on_media_time = on_media_time  # 聽譯中分頁的影片播放時間
        self.clients: set[ServerConnection] = set()
        self.audio_clients: set[ServerConnection] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        # 影片字幕翻譯一次一部，避免同時佔用 GPU
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="subs")
        self._video_jobs: dict[str, asyncio.Future] = {}

    # ---- 對外 API（任何執行緒都可呼叫） ----
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ws", daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def publish(self, ev: SubtitleEvent) -> None:
        if ev.state == "drop":
            return
        self.broadcast(ev.to_dict())

    def broadcast(self, msg: dict) -> None:
        if self.loop and self.clients:
            asyncio.run_coroutine_threadsafe(self._broadcast(msg), self.loop)

    # ---- 內部 ----
    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self) -> None:
        async with serve(self._handler, self.host, self.port, max_size=2**22,
                         process_request=self._check_origin):
            print(f"[ws] ws://{self.host}:{self.port}")
            self._ready.set()
            await asyncio.Future()

    def _check_origin(self, connection, request):
        origin = request.headers.get("Origin")
        if request.path == "/shutdown":
            # 只接受本機程式（沒有 Origin 標頭）；網頁不能叫我們關閉
            if origin or not self.on_shutdown:
                return connection.respond(403, "forbidden\n")
            threading.Thread(target=self.on_shutdown, daemon=True).start()
            return connection.respond(200, "bye\n")
        # 沒有 Origin（本機程式）或擴充功能才允許，擋掉一般網頁偷連本機服務
        if origin and not origin.startswith(ALLOWED_ORIGIN_PREFIXES):
            return connection.respond(403, "forbidden origin\n")
        return None

    async def _broadcast(self, msg: dict) -> None:
        data = json.dumps(msg, ensure_ascii=False)

        async def send_one(ws):
            try:
                # 一個卡住的用戶端不能拖住其他人
                await asyncio.wait_for(ws.send(data), 2)
            except Exception:
                self.clients.discard(ws)

        # 送聲音的連線（擴充功能的 offscreen 頁）不需要字幕
        targets = [ws for ws in self.clients if ws not in self.audio_clients]
        if targets:
            await asyncio.gather(*(send_one(ws) for ws in targets))

    async def _send(self, ws: ServerConnection, msg: dict) -> None:
        try:
            await ws.send(json.dumps(msg, ensure_ascii=False))
        except Exception:
            self.clients.discard(ws)

    async def _handler(self, ws: ServerConnection) -> None:
        self.clients.add(ws)
        await self._send(ws, {"type": "status", **self.status()})
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    self.audio_clients.add(ws)
                    if self.audio_sink:
                        self.audio_sink(raw)
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "load_video":
                    asyncio.create_task(self._load_video(ws, str(msg.get("video_id", ""))))
                elif t == "translate":
                    asyncio.create_task(self._translate(ws, msg))
                elif t == "capture":
                    self.audio_clients.add(ws)
                    if self.on_capture:
                        self.on_capture(bool(msg.get("on")), str(msg.get("title", ""))[:100])
                    await self._broadcast({"type": "status", **self.status()})
                elif t == "set_language":
                    lang = str(msg.get("lang", "auto"))
                    if self.on_language and lang in ("auto", "ja", "en"):
                        self.on_language(lang)
                    await self._broadcast({"type": "status", **self.status()})
                elif t == "set_engine":
                    eng = str(msg.get("engine", ""))
                    if self.on_engine and eng in ("whisper", "anime"):
                        self.on_engine(eng)
                elif t == "media_time":
                    if self.on_media_time:
                        self.on_media_time(msg)
                elif t == "ping":
                    await self._send(ws, {"type": "pong"})
        finally:
            self.clients.discard(ws)
            if ws in self.audio_clients:
                self.audio_clients.discard(ws)
                # 擴充功能的連線斷了（分頁關閉、Chrome 關掉）也算停止聽譯
                if self.on_capture:
                    self.on_capture(False, "")

    async def _translate(self, ws, msg: dict) -> None:
        if not self.translator:
            return
        text = str(msg.get("text", ""))[:500]
        lang = str(msg.get("lang", "ja"))
        out = await self.loop.run_in_executor(None, self.translator.translate, text, (), lang)
        await self._send(ws, {"type": "translate_result", "req": msg.get("req"), "text": out})

    async def _load_video(self, ws, video_id: str) -> None:
        from ..subtitles.youtube import IsLive, NoSubtitles, translate_video

        if not self.translator or len(video_id) != 11:
            return
        loop = self.loop

        def progress(done: int, total: int, chunk: list[Any]) -> None:
            asyncio.run_coroutine_threadsafe(self._send(ws, {
                "type": "video_cues", "video_id": video_id, "total": total,
                "done": done >= total, "cues": [c.to_dict() for c in chunk],
            }), loop)

        def job():
            return translate_video(video_id, self.translator, self.model_name, on_progress=progress)

        try:
            await loop.run_in_executor(self._pool, job)
        except IsLive:
            await self._send(ws, {"type": "video_error", "video_id": video_id, "reason": "live",
                                  "message": "直播中，改用即時聽譯"})
        except NoSubtitles:
            await self._send(ws, {"type": "video_error", "video_id": video_id, "reason": "no_subs",
                                  "message": "沒有日文字幕，改用即時聽譯"})
        except Exception as e:
            print(f"[subs] {video_id} 失敗：{e!r}")
            await self._send(ws, {"type": "video_error", "video_id": video_id, "reason": "error",
                                  "message": str(e)[:200]})
