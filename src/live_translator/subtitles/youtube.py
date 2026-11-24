"""路線 A：用 yt-dlp 取得 YouTube 日文字幕，批次翻譯並快取。"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Iterator

from ..config import ROOT
from ..translate.base import Translator
from .formats import Cue, merge_short, parse_json3

CACHE_DIR = ROOT / "cache" / "subs"
_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


class NoSubtitles(Exception):
    pass


class IsLive(Exception):
    pass


def video_id_of(url_or_id: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url_or_id):
        return url_or_id
    m = _ID_RE.search(url_or_id)
    if not m:
        raise ValueError(f"看不出影片 ID：{url_or_id}")
    return m.group(1)


def _pick(tracks: dict, keys: list[str]) -> tuple[str, dict] | None:
    for key in keys:
        for name, fmts in tracks.items():
            if name == key or (key.endswith("*") and name.startswith(key[:-1])):
                for f in fmts:
                    if f.get("ext") == "json3":
                        return name, f
    return None


def fetch_subtitles(video_id: str, langs: tuple[str, ...] = ("ja", "en")
                    ) -> tuple[list[Cue], str, bool, str]:
    """回傳 (字幕, 軌道名稱, 是否已是繁中, 原文語言)。
    優先順序：繁中官方 > 影片原本語言的官方字幕 > 其他支援語言的官方字幕 > 原本語言的自動字幕。"""
    import yt_dlp

    opts = {"skip_download": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        if info.get("is_live"):
            raise IsLive(video_id)
        subs = info.get("subtitles") or {}
        auto = info.get("automatic_captions") or {}
        orig = (info.get("language") or "").split("-")[0]
        order = [orig] + [lg for lg in langs if lg != orig] if orig in langs else list(langs)

        picked, lang = _pick(subs, ["zh-TW", "zh-Hant"]), "zh"
        already_zh = picked is not None
        if not picked:
            for lg in order:
                picked = _pick(subs, [lg, f"{lg}-*"])
                if picked:
                    lang = lg
                    break
        if not picked:
            # 自動字幕：只用影片原本語言的（YouTube 自動翻譯成別的語言的軌道品質差）
            for lg in order:
                picked = _pick(auto, [f"{lg}-orig"]) or (_pick(auto, [lg]) if lg == orig or not orig else None)
                if picked:
                    lang = lg
                    break
        if not picked:
            raise NoSubtitles(video_id)
        name, fmt = picked
        raw = ydl.urlopen(fmt["url"]).read()

    cues = parse_json3(raw)
    if already_zh:
        for c in cues:
            c.target = c.source
    elif name not in subs:  # 自動字幕比較碎，合併一下
        cues = merge_short(cues, max_chars=90 if lang == "en" else 40, sep=" " if lang == "en" else "")
    return cues, name, already_zh, lang


def _cache_path(video_id: str, model: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", model)
    return CACHE_DIR / f"{video_id}.{safe}.json"


def load_cache(video_id: str, model: str) -> list[Cue] | None:
    p = _cache_path(video_id, model)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return [Cue(**c) for c in data["cues"]]


def save_cache(video_id: str, model: str, cues: list[Cue], track: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(video_id, model).write_text(
        json.dumps({"video_id": video_id, "track": track, "cues": [c.to_dict() for c in cues]},
                   ensure_ascii=False),
        encoding="utf-8",
    )


def translate_cues(cues: list[Cue], translator: Translator, batch_size: int = 12,
                   context_size: int = 4, lang: str = "ja") -> Iterator[tuple[int, int, list[Cue]]]:
    """逐批翻譯，每完成一批 yield (起始索引, 結束索引, 該批字幕)。"""
    ctx: list[tuple[str, str]] = []
    for i in range(0, len(cues), batch_size):
        chunk = cues[i:i + batch_size]
        todo = [c for c in chunk if not c.target]
        if todo:
            targets = translator.translate_batch([c.source for c in todo], ctx[-context_size:], lang)
            for c, t in zip(todo, targets):
                c.target = t
        ctx.extend((c.source, c.target) for c in chunk)
        yield i, i + len(chunk), chunk


def translate_video(url_or_id: str, translator: Translator, model_name: str,
                    on_progress: Callable[[int, int, list[Cue]], None] | None = None,
                    use_cache: bool = True) -> list[Cue]:
    vid = video_id_of(url_or_id)
    if use_cache:
        cached = load_cache(vid, model_name)
        if cached is not None:
            if on_progress:
                on_progress(len(cached), len(cached), cached)
            return cached
    cues, track, _, lang = fetch_subtitles(vid)
    print(f"[subs] {vid}：使用字幕軌 {track}，共 {len(cues)} 句")
    for start, end, chunk in translate_cues(cues, translator, lang=lang):
        if on_progress:
            on_progress(end, len(cues), chunk)
    save_cache(vid, model_name, cues, track)
    return cues
