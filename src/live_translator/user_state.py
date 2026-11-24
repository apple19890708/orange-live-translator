"""記住使用者在選單上的選擇（例如辨識模型），下次開啟沿用。存在專案根目錄的 user_state.json。"""
from __future__ import annotations

import json

from .config import ROOT

PATH = ROOT / "user_state.json"


def load_state() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(**kv) -> None:
    data = load_state()
    data.update(kv)
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
