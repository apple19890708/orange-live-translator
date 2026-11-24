"""過濾 Whisper 在靜音、背景音樂時常見的幻聽輸出。"""
from __future__ import annotations

import re

# Whisper 日文常見幻聽（訓練資料裡影片結尾字幕）
HALLUCINATIONS = [
    "ご視聴ありがとうございました",
    "ご視聴ありがとうございます",
    "チャンネル登録",
    "最後までご視聴",
    "おやすみなさい。",
    "字幕",
    "お疲れ様でした。",
    "次回もお楽しみに",
    "次回予告",
    "ご清聴ありがとうございました",
    "Thank you for watching",
    "Thanks for watching",
    "Please subscribe",
    "Subtitles by",
    "Amara.org",
]

_REPEAT_RE = re.compile(r"(\D{1,6}?)\1{4,}")  # 不動數字，例如 1000000


def collapse_repeats(text: str) -> str:
    """「あああああああ」「はいはいはいはいはい」這種重複，縮成三次。"""
    return _REPEAT_RE.sub(lambda m: m.group(1) * 3, text)


def is_hallucination(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if re.fullmatch(r"[\s\W_]+", t):
        return True
    for h in HALLUCINATIONS:
        if h in t and len(t) <= len(h) + 6:
            return True
    return False


# 句子看起來還沒講完的結尾（日文接續助詞、英文連接詞），遇到時多等一下再切句
_JA_CONT = re.compile(r"(て|で|けど|けれど|けども|から|ので|のに|し|たら|ば|って|ながら|とか|けど|が|けれども|んで|ちゃって|だけど|でも|それで|あと)[ぇえーっ…]*$")
_EN_CONT = re.compile(
    r"(,|\b(and|but|so|because|or|the|a|an|to|of|that|with|if|when|which|who|like|is|are|was|i|we|you|my|your|in|on|for))\s*$",
    re.I)


# 助詞後面接語氣詞（「今回のね」「それはさ」「私がね」）通常是講到一半的填充，不是句尾
_JA_MID_FILLER = re.compile(r"(の|は|が|を|に|で|と|も|へ|から|けど|って)[ねさ][ぇえーっ…]*$")


def looks_incomplete(text: str, lang: str) -> bool:
    # Whisper 用「…」表示話被切斷（例：「爆音上映会アルバ…」），一定是沒講完
    if text.strip().endswith(("…", "...")):
        return True
    t = text.strip().rstrip("…").strip()
    if not t or t[-1] in "。！？!?.":
        return False
    if lang == "en":
        return bool(_EN_CONT.search(t))
    return bool(_JA_CONT.search(t) or _JA_MID_FILLER.search(t))


def clean_text(text: str) -> str:
    text = collapse_repeats(text.strip())
    return re.sub(r"\s+", " ", text)
