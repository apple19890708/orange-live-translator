"""PySide6 浮動字幕視窗：置頂、半透明、可拖曳，可切換滑鼠穿透。系統匣選單控制。"""
from __future__ import annotations

from collections import OrderedDict
from typing import Callable

from PySide6.QtCore import QObject, QPoint, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QApplication, QLabel, QMenu, QSystemTrayIcon, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..events import SubtitleEvent


class _Bridge(QObject):
    event = Signal(object)
    status = Signal(str)
    language = Signal(str)
    quit = Signal()
    engine = Signal(str)


def _make_icon(size: int = 64) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    k = size / 64
    p.setBrush(QColor("#2b6cb0"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(4 * k, 8 * k, 56 * k, 48 * k), 10 * k, 10 * k)
    p.setPen(QColor("white"))
    f = QFont("Microsoft JhengHei")
    f.setBold(True)
    f.setPixelSize(int(34 * k))
    p.setFont(f)
    p.drawText(pm.rect(), Qt.AlignCenter, "譯")
    p.end()
    return QIcon(pm)


def _fmt_time(t: float) -> str:
    t = int(t)
    return f"{t // 3600:d}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


class HistoryWindow(QWidget):
    """完整字幕紀錄：可捲動、可選取複製。停在最底下時會自動跟著新字幕捲動。"""

    def __init__(self, font_size: int = 15):
        super().__init__(None, Qt.Window | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("字幕紀錄 - Orange Live Translate")
        self.resize(640, 720)
        self.view = QTextBrowser(self)
        self.view.setOpenLinks(False)
        self.view.setStyleSheet(
            f"QTextBrowser {{ background:#161616; color:#eee; font-size:{font_size}px;"
            " font-family:'Microsoft JhengHei UI'; border:none; padding:8px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.view)
        self.settings = QSettings("live-translator", "history")
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    @staticmethod
    def _html(ev: SubtitleEvent) -> str:
        from html import escape

        tgt = escape(ev.target) if ev.state == "translated" else "<span style='color:#777'>翻譯中…</span>"
        return (f"<div style='margin-bottom:10px'><span style='color:#6a9fd8;font-size:12px'>"
                f"{_fmt_time(ev.start)}</span> <span style='color:#aaa'>{escape(ev.source)}</span><br>"
                f"<b>{tgt}</b></div>")

    def set_items(self, items: list[SubtitleEvent]) -> None:
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        old = bar.value()
        self.view.setHtml("".join(self._html(e) for e in items))
        bar.setValue(bar.maximum() if at_bottom else old)

    def closeEvent(self, e):
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(e)


class OverlayWindow(QWidget):
    def __init__(self, cfg, on_pause: Callable[[bool], None] | None = None,
                 on_quit: Callable[[], None] | None = None,
                 on_language: Callable[[str], None] | None = None, language: str = "auto",
                 on_engine: Callable[[str], None] | None = None, engine: str = "whisper"):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.cfg = cfg
        self.on_pause = on_pause
        self.on_quit = on_quit
        self.on_language = on_language
        self.on_engine = on_engine
        self.engine = engine
        self.language = language
        self.max_lines = int(cfg.get("lines", 2))
        self.show_source = bool(cfg.get("show_source", True))
        self.opacity = float(cfg.get("opacity", 0.75))
        self.items: "OrderedDict[int, SubtitleEvent]" = OrderedDict()
        # 字幕歷史（定稿、已翻譯），給滾輪往回看與「字幕紀錄」視窗用
        self.history: "OrderedDict[int, SubtitleEvent]" = OrderedDict()
        self.scroll = 0                       # 往回捲了幾句；0 = 顯示最新
        self.history_win: HistoryWindow | None = None
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._scroll_to_live)
        self._drag: QPoint | None = None
        self.click_through = False
        self.paused = False
        self.settings = QSettings("live-translator", "overlay")

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Orange Live Translate")
        self.resize(int(cfg.get("width", 1100)), 10)

        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(18, 10, 18, 10)
        self.layout_.setSpacing(2)
        self.src_font = QFont("Meiryo UI", int(cfg.get("source_font_size", 16)))
        self.tgt_font = QFont("Microsoft JhengHei UI", int(cfg.get("font_size", 26)), QFont.Bold)
        self.status_label = QLabel("")
        self.status_label.setFont(QFont("Microsoft JhengHei UI", 11))
        self.status_label.setStyleSheet("color:#9ad;")
        self.labels: list[tuple[QLabel, QLabel]] = []
        for _ in range(self.max_lines + 1):
            s, t = QLabel(), QLabel()
            for lb, font in ((s, self.src_font), (t, self.tgt_font)):
                lb.setFont(font)
                lb.setWordWrap(True)
                lb.setAlignment(Qt.AlignHCenter)
                lb.setTextFormat(Qt.PlainText)
                self.layout_.addWidget(lb)
            self.labels.append((s, t))
        self.scroll_label = QLabel("")
        self.scroll_label.setFont(QFont("Microsoft JhengHei UI", 11))
        self.scroll_label.setStyleSheet("color:#f0c060;")
        self.scroll_label.setAlignment(Qt.AlignHCenter)
        self.scroll_label.setVisible(False)
        self.layout_.addWidget(self.scroll_label)
        self.layout_.addWidget(self.status_label)

        self.bridge = _Bridge()
        self.bridge.event.connect(self._on_event)
        self.bridge.status.connect(self._on_status)
        self.bridge.language.connect(self._sync_language)
        self.bridge.quit.connect(self._quit)
        self.bridge.engine.connect(self._sync_engine)
        self._setup_tray()
        self._restore_position()
        self._render()

    # ---- 執行緒安全的入口 ----
    def push(self, ev: SubtitleEvent) -> None:
        self.bridge.event.emit(ev)

    def request_quit(self) -> None:
        """執行緒安全：從別的執行緒要求關閉程式。"""
        self.bridge.quit.emit()

    def set_status(self, text: str) -> None:
        self.bridge.status.emit(text)

    # ---- 事件處理 ----
    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))
        self._render()

    def _on_event(self, ev: SubtitleEvent) -> None:
        if ev.state == "retract":
            self.items.pop(ev.id, None)
        elif ev.state == "drop":
            cur = self.items.get(ev.id)
            if cur and cur.state == "partial":
                del self.items[ev.id]
        else:
            cur = self.items.get(ev.id)
            # 翻譯完成後不要被晚到的 partial 蓋掉
            if not (cur and cur.state == "translated" and ev.state != "translated"):
                self.items[ev.id] = ev
            self.items.move_to_end(ev.id)
            self.items = OrderedDict(sorted(self.items.items()))
        while len(self.items) > self.max_lines + 1:
            self.items.popitem(last=False)
        self._update_history(ev)
        self._render()

    def _update_history(self, ev: SubtitleEvent) -> None:
        if ev.state in ("drop", "retract"):
            self.history.pop(ev.id, None)
        elif ev.state in ("final", "translated"):
            cur = self.history.get(ev.id)
            if cur and cur.state == "translated" and ev.state != "translated":
                return
            is_new = ev.id not in self.history
            self.history[ev.id] = ev
            if is_new and self.scroll > 0:
                self.scroll += 1  # 正在看舊字幕時，新字幕進來不要讓畫面跳動
            while len(self.history) > 3000:
                self.history.popitem(last=False)
        else:
            return
        if self.history_win is not None and self.history_win.isVisible():
            self.history_win.set_items(list(self.history.values()))

    # ---- 往回看舊字幕 ----
    def wheelEvent(self, e):
        steps = int(e.angleDelta().y() / 120) or (1 if e.angleDelta().y() > 0 else -1)
        n = len(self.history)
        self.scroll = max(0, min(self.scroll + steps, max(0, n - self.max_lines)))
        if self.scroll > 0:
            self._scroll_timer.start(30_000)  # 30 秒沒動作就回到即時字幕
        self._render()

    def _scroll_to_live(self):
        self.scroll = 0
        self._render()

    def mouseDoubleClickEvent(self, e):
        self.open_history()

    def open_history(self):
        if self.history_win is None:
            self.history_win = HistoryWindow()
        self.history_win.set_items(list(self.history.values()))
        self.history_win.show()
        self.history_win.raise_()
        self.history_win.activateWindow()

    def _render(self) -> None:
        if self.scroll > 0:
            hist = list(self.history.values())
            end = len(hist) - self.scroll
            rows = hist[max(0, end - self.max_lines):end]
            self.scroll_label.setText(f"▲ 舊字幕 {end}/{len(hist)}　滾輪往下回到最新（雙擊開啟完整紀錄）")
            self.scroll_label.setVisible(True)
        else:
            self.scroll_label.setVisible(False)
            visible = list(self.items.values())
            # 最多顯示 max_lines 句定稿＋1 句辨識中
            finals = [e for e in visible if e.state != "partial"][-self.max_lines:]
            partial = [e for e in visible if e.state == "partial"][-1:]
            rows = finals + partial
        for i, (s, t) in enumerate(self.labels):
            if i < len(rows):
                ev = rows[i]
                s.setText(ev.source)
                s.setVisible(self.show_source or ev.state != "translated")
                if ev.state == "partial":
                    s.setStyleSheet("color:#bbbbbb;")
                    t.setText("")
                    t.setVisible(False)
                elif ev.state == "final":
                    s.setStyleSheet("color:#dddddd;")
                    t.setText("…")
                    t.setStyleSheet("color:#888888;")
                    t.setVisible(True)
                else:
                    s.setStyleSheet("color:#c8c8c8;")
                    t.setText(ev.target)
                    t.setStyleSheet("color:#ffffff;")
                    t.setVisible(True)
            else:
                s.setVisible(False)
                t.setVisible(False)
        self.adjustSize()

    # ---- 外觀 ----
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        p.fillPath(path, QColor(0, 0, 0, int(255 * self.opacity)))
        if not self.click_through:
            p.setPen(QColor(80, 140, 220, 160))
            p.drawPath(path)

    def sizeHint(self):
        sh = super().sizeHint()
        sh.setWidth(int(self.cfg.get("width", 1100)))
        return sh

    # ---- 拖曳 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.settings.setValue("pos", self.pos())

    def contextMenuEvent(self, e):
        self.menu.exec(e.globalPos())

    def _restore_position(self):
        pos = self.settings.value("pos")
        if isinstance(pos, QPoint):
            self.move(pos)
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.center().x() - self.width() // 2, screen.bottom() - 220)

    # ---- 系統匣 ----
    def _setup_tray(self):
        self.menu = QMenu()
        self.act_pause = QAction("暫停", self, checkable=True)
        self.act_pause.toggled.connect(self._toggle_pause)
        self.act_through = QAction("滑鼠穿透（鎖定位置）", self, checkable=True)
        self.act_through.toggled.connect(self._toggle_click_through)
        self.act_source = QAction("顯示原文", self, checkable=True)
        self.act_source.setChecked(self.show_source)
        self.act_source.toggled.connect(self._toggle_source)
        act_history = QAction("字幕紀錄…（雙擊字幕框）", self)
        act_history.triggered.connect(self.open_history)
        act_clear = QAction("清除字幕", self)
        act_clear.triggered.connect(self._clear)
        act_quit = QAction("結束", self)
        act_quit.triggered.connect(self._quit)
        # 聽譯語言（手動切換）
        lang_menu = self.menu.addMenu("聽譯語言")
        self.lang_group = QActionGroup(self)
        self.lang_actions: dict[str, QAction] = {}
        for code, label in (("auto", "自動（日文／英文）"), ("ja", "日文"), ("en", "英文")):
            a = QAction(label, self, checkable=True)
            a.setChecked(code == self.language)
            a.triggered.connect(lambda _=False, c=code: self._choose_language(c))
            self.lang_group.addAction(a)
            lang_menu.addAction(a)
            self.lang_actions[code] = a
        # 辨識模型（執行中切換）
        from ..asr.streamer import ENGINES

        eng_menu = self.menu.addMenu("辨識模型")
        self.engine_group = QActionGroup(self)
        self.engine_actions: dict[str, QAction] = {}
        for code in ("whisper", "anime"):
            a = QAction(ENGINES[code], self, checkable=True)
            a.setChecked(code == self.engine)
            a.triggered.connect(lambda _=False, c=code: self._choose_engine(c))
            self.engine_group.addAction(a)
            eng_menu.addAction(a)
            self.engine_actions[code] = a
        self.menu.addSeparator()
        for a in (act_history, self.act_pause, self.act_through, self.act_source, act_clear):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.menu.addAction(act_quit)
        self.tray = QSystemTrayIcon(_make_icon(), self)
        self.tray.setToolTip("Orange Live Translate")
        self.tray.setContextMenu(self.menu)
        self.tray.show()

    # ---- 辨識模型 ----
    def set_engine(self, kind: str) -> None:
        self.bridge.engine.emit(kind)

    def _sync_engine(self, kind: str) -> None:
        self.engine = kind
        if kind in self.engine_actions:
            self.engine_actions[kind].setChecked(True)

    def _choose_engine(self, kind: str) -> None:
        if kind == self.engine:
            return
        if self.on_engine:
            self.on_engine(kind)

    # ---- 語言 ----
    def set_language(self, lang: str) -> None:
        """執行緒安全：別處（例如 Chrome 擴充功能）切換語言時同步選單。"""
        self.bridge.language.emit(lang)

    def _sync_language(self, lang: str) -> None:
        self.language = lang
        if lang in self.lang_actions:
            self.lang_actions[lang].setChecked(True)

    def _choose_language(self, lang: str) -> None:
        self._sync_language(lang)
        if self.on_language:
            self.on_language(lang)
        names = {"auto": "自動", "ja": "日文", "en": "英文"}
        self.tray.showMessage("Orange Live Translate", f"聽譯語言：{names.get(lang, lang)}",
                              QSystemTrayIcon.Information, 1500)

    def _toggle_pause(self, on: bool):
        self.paused = on
        if self.on_pause:
            self.on_pause(on)
        self._on_status("已暫停" if on else "")

    def _toggle_click_through(self, on: bool):
        self.click_through = on
        self.setWindowFlag(Qt.WindowTransparentForInput, on)
        self.show()
        self.update()

    def _toggle_source(self, on: bool):
        self.show_source = on
        self._render()

    def _clear(self):
        # 只清畫面，字幕紀錄保留
        self.items.clear()
        self.scroll = 0
        self._render()

    def _quit(self):
        self.settings.setValue("pos", self.pos())
        if self.on_quit:
            self.on_quit()
        QApplication.quit()
