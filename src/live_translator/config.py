"""設定檔載入：config.yaml 覆蓋 config.example.yaml 的預設值。"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    """dict，但可以用 cfg.asr.model 這種寫法讀取。"""

    def __getattr__(self, name: str) -> Any:
        try:
            v = self[name]
        except KeyError as e:
            raise AttributeError(name) from e
        return Config(v) if isinstance(v, dict) else v


def load_config(path: str | Path | None = None) -> Config:
    defaults = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    user_path = Path(path) if path else ROOT / "config.yaml"
    user = {}
    if user_path.exists():
        user = yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}
    return Config(_merge(defaults, user))


def _glossary_file(path: str | Path | None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        p = ROOT / "glossary.example.yaml"
    return p if p.exists() else None


def load_corrections(path: str | Path | None) -> list[tuple[str, str]]:
    """詞彙表裡的 corrections：「常聽錯的寫法: 正確原文」，辨識完立刻替換（在顯示和翻譯之前）。
    長的先換，避免短的規則先吃掉長的。"""
    p = _glossary_file(path)
    if p is None:
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    items = [(str(k), str(v)) for k, v in (data.get("corrections") or {}).items() if k]
    return sorted(items, key=lambda kv: -len(kv[0]))


def apply_corrections(text: str, corrections: list[tuple[str, str]]) -> str:
    for wrong, right in corrections:
        if wrong in text:
            text = text.replace(wrong, right)
    return text


def load_glossary(path: str | Path | None) -> dict[str, str]:
    if not path:
        return {}
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        p = ROOT / "glossary.example.yaml"
        if not p.exists():
            return {}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(k): str(v) for k, v in (data.get("terms") or {}).items()}


def glossary_hotwords(asr_cfg, glossary: dict[str, str]) -> list[str]:
    """詞彙表裡的日文詞當作辨識提示詞。asr.glossary_hotwords: false 時不加（P3：其他主播的詞會干擾辨識）。"""
    if not bool((asr_cfg or {}).get("glossary_hotwords", True)):
        return []
    return [k for k in glossary if any(ord(c) > 0x3000 for c in k)]
