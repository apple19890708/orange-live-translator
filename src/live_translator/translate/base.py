"""翻譯層介面。要換翻譯來源（本機 Ollama、雲端 API）只要改 config 的 translate.provider。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from .postprocess import postprocess

LANG_NAMES = {"ja": "日文", "en": "英文", "ko": "韓文", "zh": "中文"}

FILLERS = {
    "ja": "「えっと」「あの」「なんか」",
    "en": "「um」「uh」「like」「you know」",
}


def system_prompt(lang: str) -> str:
    name = LANG_NAMES.get(lang, "外文")
    fillers = FILLERS.get(lang, "口頭禪")
    return f"""你是專業的{name}翻中字幕譯者，負責即時翻譯直播主、VTuber、影片的語音辨識結果。
規則：
- 只輸出「目前這句」的繁體中文（台灣用語）譯文，不要解釋、不要加引號、不要附原文。
- 語音辨識可能有錯字或斷句不完整，請依前文推測合理意思。
- 原文可能夾雜英文單字或英文句子，英文部分也要一起翻成自然的中文，不要保留英文（專有名詞、歌名除外）。
- 口語自然簡潔，像台灣字幕組。{fillers}等口頭禪可省略。
- 人名、專有名詞依詞彙表；沒有的人名保留原文或用常見譯名。
- 如果這句只有語助詞或無意義，輸出原意的簡短中文（例如「嗯」「欸？」）。"""


def batch_system_prompt(lang: str) -> str:
    name = LANG_NAMES.get(lang, "外文")
    return f"""你是專業的{name}翻中字幕譯者。使用者會給你編號的{name}字幕行，請逐行翻成繁體中文（台灣用語）。
規則：
- 輸出行數必須與輸入相同，每行格式為「編號|譯文」，不要多餘說明。
- 字幕常把一句話拆成多行，請參考上下文讓譯文通順，但每行只放該行對應的內容。
- 原文可能夾雜英文，英文部分也要翻成中文（專有名詞、歌名除外）。
- 口語自然簡潔，像台灣字幕組。人名、專有名詞依詞彙表。"""


# 舊名稱相容
SYSTEM_PROMPT = system_prompt("ja")
BATCH_SYSTEM_PROMPT = batch_system_prompt("ja")


def relevant_glossary(glossary: dict[str, str], texts: Sequence[str]) -> dict[str, str]:
    """只留原文裡真的出現的詞。全部塞進提示詞的話，模型會把詞彙表的詞亂套到無關的句子。"""
    joined = " ".join(texts)
    return {k: v for k, v in glossary.items() if k and k in joined}


def glossary_block(glossary: dict[str, str]) -> str:
    if not glossary:
        return ""
    return "詞彙表（原文 → 譯文）：\n" + "\n".join(f"- {k} → {v}" for k, v in glossary.items())


def build_user_prompt(text: str, context: Sequence[tuple[str, str]], glossary: dict[str, str]) -> str:
    parts = []
    g = glossary_block(glossary)
    if g:
        parts.append(g)
    if context:
        parts.append("前文（原文 → 譯文）：\n" + "\n".join(f"- {s} → {t}" for s, t in context))
    parts.append(f"目前這句：\n{text}")
    return "\n\n".join(parts)


class Translator(ABC):
    def __init__(self, cfg, glossary: dict[str, str]):
        self.cfg = cfg
        self.glossary = glossary
        self.opencc = cfg.get("opencc") or None

    @abstractmethod
    def chat(self, system: str, user: str) -> str:
        """送出一次對話，回傳模型原始輸出。"""

    def translate(self, text: str, context: Sequence[tuple[str, str]] = (), lang: str = "ja") -> str:
        raw = self.chat(system_prompt(lang), build_user_prompt(text, context, relevant_glossary(self.glossary, [text])))
        return postprocess(raw, self.opencc, self.glossary)

    def translate_batch(self, lines: Sequence[str], context: Sequence[tuple[str, str]] = (),
                        lang: str = "ja") -> list[str]:
        """一次翻多行（字幕檔用）。解析失敗的行退回逐句翻譯。"""
        if not lines:
            return []
        parts = []
        g = glossary_block(relevant_glossary(self.glossary, lines))
        if g:
            parts.append(g)
        if context:
            parts.append("前文（已翻譯，僅供參考）：\n" + "\n".join(f"- {s} → {t}" for s, t in context))
        parts.append("請翻譯：\n" + "\n".join(f"{i + 1}|{ln}" for i, ln in enumerate(lines)))
        raw = self.chat(batch_system_prompt(lang), "\n\n".join(parts))
        result = parse_batch(raw, len(lines))
        out = []
        for i, src in enumerate(lines):
            t = result.get(i + 1)
            if t is None:
                out.append(self.translate(src, context, lang))
            else:
                out.append(postprocess(t, self.opencc, self.glossary))
        return out

    def unload(self) -> None:
        """釋放模型佔用的資源；雲端 API 不需要。"""

    def all_models(self) -> list[str]:
        m = getattr(self, "model", "")
        return [m] if m else []

    def warmup(self) -> None:
        try:
            self.translate("こんにちは")
        except Exception as e:
            print(f"[translate] warmup failed: {e!r}")


def parse_batch(raw: str, n: int) -> dict[int, str]:
    from .postprocess import _THINK_RE

    raw = _THINK_RE.sub("", raw)
    out: dict[int, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        num, _, text = line.partition("|")
        num = num.strip().lstrip("#").rstrip(".")
        if num.isdigit() and 1 <= int(num) <= n and text.strip():
            out.setdefault(int(num), text.strip())
    return out


def build_translator(cfg, glossary: dict[str, str]) -> Translator:
    """cfg.models 有設定（例如 {ja: sakura, en: qwen3:8b}）時，依語言選模型；否則全部用 cfg.model。"""
    models = dict(cfg.get("models") or {})
    if models:
        from .router import RoutingTranslator

        default = _build_single(cfg, glossary)
        by_lang = {}
        for lang, model in models.items():
            if not model:
                continue
            sub = dict(cfg)
            sub["model"] = model
            sub.pop("models", None)
            by_lang[lang] = _build_single(sub, glossary)
        return RoutingTranslator(cfg, glossary, by_lang, default)
    return _build_single(cfg, glossary)


def _build_single(cfg, glossary: dict[str, str]) -> Translator:
    provider = (cfg.get("provider") or "ollama").lower()
    if provider == "ollama":
        if "sakura" in str(cfg["model"]).lower():
            from .sakura import SakuraTranslator

            return SakuraTranslator(cfg, glossary)
        if any(k in str(cfg["model"]).lower() for k in ("hy-mt", "hunyuan-mt")):
            from .hymt import HyMTTranslator

            return HyMTTranslator(cfg, glossary)
        from .ollama import OllamaTranslator

        return OllamaTranslator(cfg, glossary)
    if provider in ("openai", "openrouter"):
        from .openai_compat import OpenAICompatTranslator

        return OpenAICompatTranslator(cfg, glossary)
    raise ValueError(f"未知的翻譯 provider：{provider}")
