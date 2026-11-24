"""口頭禪（語癖）處理：翻譯前去掉日文語助詞，翻譯後去掉中文的「呃、嗯、那個」與「——」。

使用者回饋：「恩、啊、那個」不需要翻出來，「——」太常出現。
- 只去掉「獨立」的語助詞（前後是標點或句首）；「あの人」「その子」「好難啊」這種有意義的不動。
- 畫面上的日文原文不變，只影響送去翻譯的文字與譯文。
"""
from __future__ import annotations

import re

# 日文：句首或標點後、而且後面接標點／長音的語助詞
_JA_FILLER = re.compile(
    r"(?:(?<=^)|(?<=[、。！？!?\s…]))"
    r"(?:えーっと|えーと|ええと|えっと|えー+|ええ(?=[、…])|あのー+|あの(?=[、，\s…])|そのー+|その(?=[、，\s…])"
    r"|なんか(?=[、，\s…])|まあ(?=[、，\s])|んー+|うーん+|うん(?=[、\s])|ん+っ?(?=[、。…！？!?]|$)|はい(?=[、])|こう(?=[、]))"
    r"[ー〜、，\s…]*"
)
_JA_LONE = {"で", "あ", "え", "お", "じゃあ", "じゃ", "はい", "うん", "あっ", "で、えと", "それで"}
_JA_ONLY_PUNCT = re.compile(r"^[\s、。！？!?…ー〜・,.]*$")

# 中文：獨立的語助詞（句首或標點後，後面接標點、破折號或結尾）
_ZH_FILLER = re.compile(
    r"(?:(?<=^)|(?<=[，。！？、…；\s]))"
    r"(?:呃+|嗯+|欸+|誒+|額+|唔+|哎+|嘛|啊+|那個|這個嘛|就是說|怎麼說呢)"
    r"(?:[——…～~，、\s]+|(?=[。！？])|$)"
)
_DASH = re.compile(r"—+|─+|－{2,}")


def strip_ja_fillers(text: str) -> str:
    """送去翻譯前，去掉日文口頭禪。整句都是口頭禪時回傳空字串。"""
    out = text
    for _ in range(3):  # 「えー、えっと、あの、」連續出現
        new = _JA_FILLER.sub("", out)
        if new == out:
            break
        out = new
    out = out.lstrip("、，。 ")
    if _JA_ONLY_PUNCT.match(out):
        return ""
    # 只剩「で、」「あ、」這種接續詞／感嘆詞，也當成沒有內容
    if re.sub(r"[\s、。！？!?…ー〜]", "", out) in _JA_LONE:
        return ""
    return out


def strip_zh_fillers(text: str) -> str:
    """翻譯後，去掉中文口頭禪，並把「——」換成逗號（句尾則刪掉）。"""
    out = text
    for _ in range(3):
        new = _ZH_FILLER.sub("", out)
        if new == out:
            break
        out = new
    out = _DASH.sub("，", out)
    out = re.sub(r"，\s*([，。！？…])", r"\1", out)   # 「，。」→「。」
    out = re.sub(r"[，、]{2,}", "，", out)
    out = re.sub(r"，+$", "", out.strip())
    return out.lstrip("，、。 ").strip()
