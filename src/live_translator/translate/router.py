"""依原文選翻譯模型：純日文用 Sakura；夾雜英文或純英文用 translate.model（預設 Hy-MT2-1.8B）。

Sakura 是日翻中專用模型，遇到英文會原封不動留著（例：「what is this?」不翻），
所以只要句子裡有真正的英文單字，就改交給通用模型。
"""
from __future__ import annotations

import re
from typing import Sequence

from .base import Translator

_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z']+")


def has_english(text: str) -> bool:
    """有英文單字（不算 CD、MV、VIP 這種全大寫縮寫，也不算 cm、kg 這種單位）。"""
    words = [w for w in _LATIN_WORD.findall(text) if not (w.isupper() and len(w) <= 4)]
    return sum(len(w) for w in words) >= 4


def text_lang(text: str, asr_lang: str) -> str:
    """實際送翻譯時用的語言：Whisper 判斷是日文，但內容其實全是英文時改成 en。"""
    letters = [c for c in text if c.isalpha()]
    if letters:
        latin = sum(1 for c in letters if c.isascii())
        # 一個日文字的資訊量約等於三個英文字母
        weighted = latin + 3 * (len(letters) - latin)
        if latin / weighted >= 0.6:
            return "en"
    return asr_lang


class RoutingTranslator(Translator):
    def __init__(self, cfg, glossary, by_lang: dict[str, Translator], default: Translator):
        super().__init__(cfg, glossary)
        self.by_lang = by_lang
        self.default = default

    def pick(self, lang: str, text: str = "") -> Translator:
        if text and has_english(text):
            return self.default
        return self.by_lang.get(lang, self.default)

    def chat(self, system: str, user: str) -> str:
        return self.default.chat(system, user)

    def translate(self, text: str, context: Sequence[tuple[str, str]] = (), lang: str = "ja") -> str:
        lang = text_lang(text, lang)
        first = self.pick(lang, text)
        try:
            return first.translate(text, context, lang)
        except Exception as e:
            # 這個模型逾時或出錯：改用另一個模型翻，字幕不要整個卡住
            backup = self.default if first is not self.default else self.by_lang.get(lang)
            if backup is None or backup is first:
                raise
            print(f"[translate] {getattr(first, 'model', '')} 失敗（{e.__class__.__name__}），改用 {getattr(backup, 'model', '')}")
            return backup.translate(text, context, lang)

    def translate_other(self, text: str, context: Sequence[tuple[str, str]] = (), lang: str = "ja") -> str | None:
        """用「另一個」模型翻（第一個模型的譯文殘留假名等問題時重翻用）；沒有另一個模型時回傳 None。"""
        lang = text_lang(text, lang)
        first = self.pick(lang, text)
        other = self.default if first is not self.default else self.by_lang.get(lang)
        if other is None or other is first:
            return None
        return other.translate(text, context, lang)

    def translate_batch(self, lines: Sequence[str], context: Sequence[tuple[str, str]] = (),
                        lang: str = "ja") -> list[str]:
        # 同一批裡，純日文交給 Sakura、夾英文的交給通用模型，最後依原順序合併
        groups: dict[int, list[int]] = {}
        picked: dict[int, Translator] = {}
        for i, ln in enumerate(lines):
            t = self.pick(text_lang(ln, lang), ln)
            groups.setdefault(id(t), []).append(i)
            picked[id(t)] = t
        out: list[str] = [""] * len(lines)
        for key, idxs in groups.items():
            res = picked[key].translate_batch([lines[i] for i in idxs], context, lang)
            for i, r in zip(idxs, res):
                out[i] = r
        return out

    def warmup(self) -> None:
        # 只暖機預設語言的模型；另一個用到時才載入，避免兩個同時佔顯示記憶體
        self.by_lang.get(self.cfg.get("warmup_lang", "ja"), self.default).warmup()

    def unload(self) -> None:
        for t in {id(t): t for t in [self.default, *self.by_lang.values()]}.values():
            t.unload()

    def all_models(self) -> list[str]:
        return list(dict.fromkeys(getattr(t, "model", "") for t in [self.default, *self.by_lang.values()]))

    def model_for(self, lang: str) -> str:
        return getattr(self.by_lang.get(lang, self.default), "model", "")
