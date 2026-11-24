"""翻譯工作執行緒：收定稿句子，附前文翻譯，發出 translated 事件。"""
from __future__ import annotations

import queue
import re
import threading
import time
from collections import OrderedDict, deque
from typing import Callable

from ..events import SubtitleEvent
from .base import Translator
from .quality import normalize_source, problems

_MEANINGFUL = re.compile(r"[\w぀-ヿ一-鿿]")


class TranslationWorker:
    def __init__(self, translator: Translator, on_event: Callable[[SubtitleEvent], None],
                 context_sentences: int = 4, memory_size: int = 0):
        self.translator = translator
        self.on_event = on_event
        self.context: deque[tuple[str, str]] = deque(maxlen=max(0, context_sentences))
        # 翻譯記憶：原文（正規化後）→ 第一次的譯文。0 = 關閉（預設；實測拉低翻譯分數，E43）
        self.memory_size = max(0, int(memory_size))
        self.memory: "OrderedDict[str, str]" = OrderedDict()
        self.q: "queue.Queue[SubtitleEvent]" = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_latency = 0.0
        self._retracted: set[int] = set()

    def submit(self, ev: SubtitleEvent) -> None:
        self._retracted.discard(ev.id)  # 合併重聽失敗時，同一句會再送一次
        self.q.put(ev)

    def retract(self, ev: SubtitleEvent) -> None:
        self._retracted.add(ev.id)
        self.context = type(self.context)([c for c in self.context if c[0] != ev.source],
                                          maxlen=self.context.maxlen)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="translate", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ev = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            if ev.id in self._retracted:
                continue
            # 積壓太多時把連續的句子合併成一次翻譯，追上進度
            batch = [ev]
            while len(batch) < 3 and self.q.qsize() >= 2:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            for item in self._translate(batch):
                self.on_event(item)

    def _translate(self, batch: list[SubtitleEvent]) -> list[SubtitleEvent]:
        from .fillers import strip_ja_fillers

        out_events: list[SubtitleEvent] = []
        keep = []
        for ev in batch:
            mt = strip_ja_fillers(ev.source) if (ev.lang or "ja") == "ja" else ev.source
            if not mt:
                # 整句都是「えー」「んー」這種口頭禪：不翻譯、從畫面收掉
                out_events.append(SubtitleEvent(ev.id, "retract", ev.source, lang=ev.lang))
                continue
            keep.append((ev, mt))
        if keep:
            out_events += self._translate_texts(keep)
        return out_events

    def _translate_texts(self, items: list[tuple[SubtitleEvent, str]]) -> list[SubtitleEvent]:
        batch = [ev for ev, _ in items]
        texts = [mt for _, mt in items]
        t0 = time.perf_counter()
        targets: list[str] = [""] * len(batch)
        clean = [False] * len(batch)
        # 同一句原文（例：往回拉時間軸又聽到一次）沿用第一次的譯文，不要每次翻法不同
        todo = []
        for i, mt in enumerate(texts):
            hit = self.memory.get(normalize_source(mt)) if self.memory_size else None
            if hit is not None:
                targets[i], clean[i] = hit, True
            else:
                todo.append(i)
        # 前文裡不放「和這句相同的原文」：Sakura 看到前文已有同一句時，會把前文和提示詞整段吐出來（I15）
        keys = {normalize_source(texts[i]) for i in todo}
        ctx = [c for c in self.context if normalize_source(c[0]) not in keys]
        langs = {batch[i].lang or "ja" for i in todo}
        try:
            if len(todo) == 1:
                i = todo[0]
                targets[i] = self.translator.translate(texts[i], ctx, batch[i].lang or "ja")
            elif todo and len(langs) == 1:
                res = self.translator.translate_batch([texts[i] for i in todo], ctx, langs.pop())
                for i, r in zip(todo, res):
                    targets[i] = r
            else:  # 中英日混在一起就逐句翻
                for i in todo:
                    targets[i] = self.translator.translate(texts[i], ctx, batch[i].lang or "ja")
            for i in todo:
                targets[i], clean[i] = self._repair(texts[i], targets[i], ctx, batch[i].lang or "ja")
        except Exception as e:
            print(f"[translate] 失敗：{e!r}")
            for i in todo:
                targets[i], clean[i] = "（翻譯失敗）", False
        self.last_latency = time.perf_counter() - t0
        out = []
        for ev, mt, tgt, ok in zip(batch, texts, targets, clean):
            if ev.id in self._retracted:  # 翻譯途中被收回
                continue
            if ok:
                # 有問題的譯文（殘留假名、吐出提示詞、翻譯失敗）不放進前文和記憶，避免傳染給下一句
                self.context.append((mt, tgt))
                self._remember(mt, tgt)
            if not _MEANINGFUL.search(tgt or ""):
                # 譯文只剩標點（例：「んんっ!」→「！？」）：沒有內容，從畫面收掉
                out.append(SubtitleEvent(ev.id, "retract", ev.source, lang=ev.lang))
                continue
            out.append(SubtitleEvent(ev.id, "translated", ev.source, tgt, ev.start, ev.end, ev.lang))
        return out

    def _remember(self, mt: str, tgt: str) -> None:
        if not self.memory_size:
            return
        key = normalize_source(mt)
        self.memory[key] = tgt
        self.memory.move_to_end(key)
        while len(self.memory) > self.memory_size:
            self.memory.popitem(last=False)

    def _repair(self, text: str, tgt: str, ctx: list[tuple[str, str]], lang: str) -> tuple[str, bool]:
        """譯文明顯壞掉時重翻：先不帶前文，再換另一個模型。回傳（譯文, 是否沒問題）。"""
        # 詞彙表指定保留原文的詞（例：ウタノ→ウタノ）不算殘留假名
        keep = [v for k, v in (getattr(self.translator, "glossary", None) or {}).items() if k and v and k in text]
        issues = problems(tgt, text, ctx, keep)
        if not issues:
            return tgt, True
        tries = [(tgt, issues)]
        attempts = [lambda: self.translator.translate(text, [], lang)]
        other = getattr(self.translator, "translate_other", None)
        if other:
            attempts.append(lambda: other(text, ctx, lang))
        for attempt in attempts:
            try:
                alt = attempt()
            except Exception:
                continue
            if alt is None:
                continue
            alt_issues = problems(alt, text, ctx, keep)
            if not alt_issues:
                print(f"[translate] 重翻（{'、'.join(issues)}）：{tgt[:40]} → {alt[:40]}")
                return alt, True
            tries.append((alt, alt_issues))
        # 都有問題：吐出提示詞最糟，其次看問題數
        best, best_issues = min(tries, key=lambda x: ("leak" in x[1], len(x[1])))
        if "leak" in best_issues:
            return "（翻譯失敗）", False
        return best, False
