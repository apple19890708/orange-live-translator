"""SakuraLLM（Sakura-GalTransl）日翻中專用模型，透過 Ollama 執行。

依官方 v3 模板組提示詞：歷史翻譯 + 術語表（src->dst）+ 原文。輸出是簡體，後處理再轉台灣正體。
只支援日文；其他語言請用一般模型（見 router.py）。
"""
from __future__ import annotations

from typing import Sequence

from .base import relevant_glossary
from .ollama import OllamaTranslator
from .postprocess import _THINK_RE, postprocess, to_traditional

SAKURA_SYSTEM = (
    "你是一个视觉小说翻译模型，可以通顺地使用给定的术语表以指定的风格将日文翻译成简体中文，"
    "并联系上下文正确使用人称代词，注意不要混淆使役态和被动态的主语和宾语，"
    "不要擅自添加原文中没有的特殊符号，也不要擅自增加或减少换行。"
)


class SakuraTranslator(OllamaTranslator):
    def default_options(self) -> dict:
        # 官方建議 0.3；即時字幕更重視穩定（同一個詞不要每次翻法不同），所以調低
        return {"temperature": 0.1, "top_p": 0.8, "num_ctx": 4096, "repeat_penalty": 1.05}

    def _glossary_lines(self, lines: Sequence[str]) -> str:
        # 只放這幾句真的出現的詞（全部放進去，Sakura 會把無關的詞亂套進譯文，例如「たくさん」→「笑死」）
        # 譯文給 Sakura 看簡體比較自然；最後輸出還是會轉回台灣用語並強制套用
        g = relevant_glossary(self.glossary, lines)
        return "\n".join(f"{k}->{to_traditional(v, 't2s')}" for k, v in g.items())

    def _prompt(self, lines: Sequence[str], context: Sequence[tuple[str, str]]) -> str:
        parts = []
        if context:
            history = "\n".join(to_traditional(t, "t2s") for _s, t in context if t)
            parts.append(f"历史翻译：{history}")
        parts.append("参考以下术语表（可为空，格式为src->dst #备注）：")
        parts.append(self._glossary_lines(lines))
        parts.append("根据以上术语表的对应关系和备注，结合历史剧情和上下文，将下面的文本从日文翻译成简体中文：")
        parts.append("\n".join(ln.replace("\n", " ") for ln in lines))
        return "\n".join(parts)

    def translate(self, text: str, context: Sequence[tuple[str, str]] = (), lang: str = "ja") -> str:
        raw = self.chat(SAKURA_SYSTEM, self._prompt([text], context))
        raw = _THINK_RE.sub("", raw).strip()
        # 單句：模型偶爾會換行，合併成一行
        raw = " ".join(ln.strip() for ln in raw.splitlines() if ln.strip())
        return postprocess(raw, self.opencc, self.glossary)

    def translate_batch(self, lines: Sequence[str], context: Sequence[tuple[str, str]] = (),
                        lang: str = "ja") -> list[str]:
        if not lines:
            return []
        raw = _THINK_RE.sub("", self.chat(SAKURA_SYSTEM, self._prompt(lines, context))).strip()
        out_lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if len(out_lines) != len(lines):
            # 行數對不上就逐句翻，確保字幕時間軸正確
            ctx = list(context)
            result = []
            for ln in lines:
                t = self.translate(ln, ctx[-4:])
                ctx.append((ln, t))
                result.append(t)
            return result
        return [postprocess(t, self.opencc, self.glossary) for t in out_lines]
