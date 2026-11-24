"""騰訊 Hy-MT2（Hunyuan-MT 新一代）翻譯專用模型，透過 Ollama 執行。

依官方模型卡的提示格式（https://huggingface.co/tencent/Hy-MT2-7B-GGUF）：
- 預設翻譯：「将以下文本翻译为{目標語言}，注意只需要输出翻译后的结果，不要额外解释：\n\n{原文}」
- 術語：前面加「参考下面的翻译：\n{原文} 翻译成 {譯文}」
- 前文：用「【背景信息】…【待翻译文本】」格式
輸出簡體，和 Sakura 一樣交給後處理轉台灣正體。
"""
from __future__ import annotations

from typing import Sequence

from .base import relevant_glossary
from .ollama import OllamaTranslator
from .postprocess import _THINK_RE, postprocess, to_traditional

TARGET = "中文"

# Hy-MT2-1.8B 的特殊字元（GGUF 詞表裡的原樣）
RAW_BOS = "<｜hy_begin▁of▁sentence｜>"
RAW_USER = "<｜hy_User｜>"
RAW_ASSISTANT = "<｜hy_Assistant｜>"
RAW_STOPS = ("<｜hy_place▁holder▁no▁2｜>", "<｜hy_EOT｜>", "<｜hy_end▁of▁sentence｜>")


class HyMTTranslator(OllamaTranslator):
    def default_options(self) -> dict:
        # 官方建議 top_k 20、top_p 0.6、repetition_penalty 1.05；溫度調低讓同一句譯法穩定
        return {"temperature": float(self.cfg.get("temperature", 0.1)), "top_p": 0.6, "top_k": 20,
                "repeat_penalty": 1.05, "num_ctx": 4096}

    def _prompt(self, text: str, context: Sequence[tuple[str, str]]) -> str:
        parts = []
        g = relevant_glossary(self.glossary, [text])
        if g:
            parts.append("参考下面的翻译：\n" + "\n".join(f"{k} 翻译成 {to_traditional(v, 't2s')}" for k, v in g.items()))
        src = text.replace("\n", " ")
        if context:
            bg = "\n".join(s for s, _t in context if s)
            parts.append(f"【背景信息】\n{bg}\n\n请结合背景信息将以下文本翻译为{TARGET}，"
                         f"注意只需要输出翻译后的结果，不要额外解释。\n\n【待翻译文本】\n{src}")
        else:
            parts.append(f"将以下文本翻译为{TARGET}，注意只需要输出翻译后的结果，不要额外解释：\n\n{src}")
        return "\n\n".join(parts)

    def chat(self, system: str, user: str, options: dict | None = None) -> str:
        # Hy-MT 的提示全部放在 user 訊息裡（官方範例沒有 system）
        options = dict(options or {})
        options.setdefault("seed", int(self.cfg.get("seed", 42)))
        options.setdefault("num_predict", min(512, 64 + 2 * len(user)))
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": user}],
            "stream": False,
            "keep_alive": "30m",
            "options": {**self.default_options(), **options},
        }
        if self._raw():
            # 1.8B 的官方 GGUF 在 Ollama 裡自動產生的對話範本是壞的（只輸出「onse」之類的碎片），
            # 改用 raw 模式自己組官方格式（E66）
            body.pop("messages")
            body["prompt"] = f"{RAW_BOS}{RAW_USER}{user}{RAW_ASSISTANT}"
            body["raw"] = True
            body["options"]["stop"] = list(RAW_STOPS)
            r = self.client.post(f"{self.base_url}/api/generate", json=body)
            r.raise_for_status()
            return r.json()["response"]
        r = self.client.post(f"{self.base_url}/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]["content"]

    def _raw(self) -> bool:
        v = self.cfg.get("raw_prompt", "auto")
        if isinstance(v, bool):
            return v
        return "1.8b" in str(self.model).lower()

    def translate(self, text: str, context: Sequence[tuple[str, str]] = (), lang: str = "ja") -> str:
        raw = _THINK_RE.sub("", self.chat("", self._prompt(text, context))).strip()
        raw = " ".join(ln.strip() for ln in raw.splitlines() if ln.strip())
        return postprocess(raw, self.opencc, self.glossary)

    def translate_batch(self, lines: Sequence[str], context: Sequence[tuple[str, str]] = (),
                        lang: str = "ja") -> list[str]:
        # 沒有多行格式：逐句翻，前文接著累積
        ctx = list(context)
        out = []
        for ln in lines:
            t = self.translate(ln, ctx[-4:], lang)
            ctx.append((ln, t))
            out.append(t)
        return out
