"""翻譯後處理：清理模型輸出、簡轉繁（台灣用語）、詞彙表強制替換。"""
from __future__ import annotations

import re
from functools import lru_cache

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_PREFIX_RE = re.compile(r"^\s*(譯文|翻譯|中文|翻译|译文|目前這句|目前这句|當前句子)\s*[:：]\s*")


@lru_cache(maxsize=4)
def _opencc(conf: str):
    import opencc

    return opencc.OpenCC(conf)


def clean_output(text: str) -> str:
    text = _THINK_RE.sub("", text).strip()
    # 只取第一段，避免模型多話附上解釋
    text = text.split("\n\n")[0].strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    text = lines[0] if len(lines) == 1 else " ".join(lines)
    text = _PREFIX_RE.sub("", text)
    if len(text) >= 2 and text[0] in "「\"“" and text[-1] in "」\"”":
        text = text[1:-1]
    return text.strip()


def to_traditional(text: str, conf: str | None) -> str:
    if not conf or not text:
        return text
    return _opencc(conf).convert(text)


# OpenCC s2twp 的台灣用語轉換偶爾轉錯，轉完再修正
_TW_FIXES = {"釋出會": "發表會", "釋出了新": "發表了新"}


# OpenCC 有時把副詞「只」轉成量詞「隻」（只有→隻有）。前面是數字、這、那、每時是真的量詞，不動。
_ZHI_RE = re.compile(r"(?<![一二兩三四五六七八九十百幾每這那半單0-9])隻(?=有|是|要|能|好|會|不過|剩|差|需|想|在|看|說|顧)")


# 輸入已經是繁體時，s2twp 會把「干擾、干預、干涉」轉成「幹…」（參考 jt-live-whisper 的保護清單）
_GAN_RE = re.compile(r"幹(?=擾|預|涉)")


def fix_tw_terms(text: str) -> str:
    for a, b in _TW_FIXES.items():
        text = text.replace(a, b)
    text = _GAN_RE.sub("干", text)
    # 台灣字幕慣用「台」（台北、台上、台灣），OpenCC 會轉成正式寫法「臺」
    text = text.replace("臺", "台")
    return _ZHI_RE.sub("只", text)


def apply_glossary(text: str, glossary: dict[str, str]) -> str:
    """模型偶爾會把原文詞直接留著，這裡補換掉。"""
    for src, dst in glossary.items():
        if src and src in text:
            text = text.replace(src, dst)
    return text


def postprocess(text: str, opencc_conf: str | None, glossary: dict[str, str]) -> str:
    text = clean_output(text)
    text = fix_tw_terms(to_traditional(text, opencc_conf))
    from .fillers import strip_zh_fillers

    text = strip_zh_fillers(text)
    # 詞彙表譯文本身是繁體，轉換後再套用，避免被 OpenCC 改掉
    return apply_glossary(text, glossary)
