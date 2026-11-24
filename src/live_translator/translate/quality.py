"""譯文品質檢查：抓出明顯壞掉的譯文，讓翻譯工作執行緒重翻。

1. 吐出提示詞（I15）：Sakura 在「前文裡已經有同一句」時，會把整段歷史譯文和提示詞
   （「參考以下術語表…翻譯成簡體中文：」）當成譯文輸出。
2. 殘留日文假名：不認得的片假名（ジューツー）原樣留下。
3. 殘留羅馬拼音：句子被切在詞中間時，碎片（ますー → masu）被拼成羅馬字。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Sequence

# Sakura 官方模板裡的字（簡體與轉換後的繁體都要抓）
_LEAK_MARKERS = ("术语表", "術語表", "历史翻译", "歷史翻譯", "翻译成简体", "翻譯成簡體",
                 "src->dst", "历史剧情", "歷史劇情")
_KANA = re.compile(r"[ぁ-ゖァ-ヺ]{2,}")
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
_LATIN_ANY = re.compile(r"[A-Za-z]")


def normalize_source(text: str) -> str:
    """比對「同一句原文」用：全形半形統一、去掉空白。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def leaked_prompt(target: str, context: Sequence[tuple[str, str]] = ()) -> bool:
    """譯文裡有提示詞，或抄了兩句以上的前文譯文。"""
    t = target or ""
    if any(m in t for m in _LEAK_MARKERS):
        return True
    copied = sum(1 for _s, ct in context if ct and len(ct) >= 4 and ct in t)
    return copied >= 2


def leftover_kana(target: str) -> bool:
    return bool(_KANA.search(target or ""))


# 日文音節組成的小寫字（masu、desu、adio）：句子被切在詞中間時，碎片被拼成羅馬字。
# 英文歌名、專有名詞（Shout、Breaker、Macross）是正確的譯法，不能當成問題（實測一起重翻會讓 chrF 26.5→25.3）。
_ROMAJI = re.compile(r"(?:(?:[kgsztdnhbpmrfj]|sh|ch|ts|ky|gy|ny|hy|my|ry|by|py)?[aiueo]|n)+")


def leftover_romaji(target: str, source: str) -> bool:
    """原文沒有英文字母，譯文卻冒出日文音節拼成的小寫羅馬字。"""
    if _LATIN_ANY.search(source or ""):
        return False
    return any(len(w) >= 4 and w.islower() and _ROMAJI.fullmatch(w) for w in _LATIN_WORD.findall(target or ""))


def problems(target: str, source: str, context: Sequence[tuple[str, str]] = (),
             allowed: Sequence[str] = ()) -> list[str]:
    """回傳這句譯文的問題（空清單 = 沒問題）。allowed：詞彙表指定的譯文（可能刻意保留假名），檢查前先拿掉。"""
    for w in sorted(allowed, key=len, reverse=True):
        target = (target or "").replace(w, "")
    out = []
    if leaked_prompt(target, context):
        out.append("leak")
    if leftover_kana(target):
        out.append("kana")
    if leftover_romaji(target, source):
        out.append("romaji")
    return out
