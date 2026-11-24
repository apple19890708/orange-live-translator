"""本機 Ollama 翻譯。"""
from __future__ import annotations

import httpx

from .base import Translator


class OllamaTranslator(Translator):
    def __init__(self, cfg, glossary):
        super().__init__(cfg, glossary)
        self.base_url = (cfg.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
        self.model = cfg["model"]
        # 即時字幕不能等太久：模型卡住（一直生成不停）時 45 秒內放棄，改用備援模型
        self.client = httpx.Client(timeout=httpx.Timeout(45.0, connect=5.0))
        self._supports_think = True

    def unload(self) -> None:
        """讓 Ollama 立刻把模型移出顯示記憶體（關閉程式時呼叫）。"""
        try:
            self.client.post(f"{self.base_url}/api/generate",
                             json={"model": self.model, "keep_alive": 0}, timeout=5)
        except Exception:
            pass

    def default_options(self) -> dict:
        return {"temperature": float(self.cfg.get("temperature", 0.2)), "num_ctx": 4096}

    def chat(self, system: str, user: str, options: dict | None = None) -> str:
        options = dict(options or {})
        # 固定隨機種子：同樣的輸入每次得到同樣的譯文（評分才能比較；即時字幕也比較穩定）
        options.setdefault("seed", int(self.cfg.get("seed", 42)))
        # 輸出長度上限：一般譯文遠低於此；偶爾模型會重複生成停不下來，靠這個保證會結束
        options.setdefault("num_predict", min(1024, 128 + len(user)))
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "keep_alive": "30m",     # 模型常駐顯示記憶體
            "options": {**self.default_options(), **options},
        }
        if self._supports_think:
            body["think"] = False    # Qwen3 等推理模型：關掉思考，降低延遲
        r = self.client.post(f"{self.base_url}/api/chat", json=body)
        if r.status_code == 400 and "think" in r.text:
            # 不支援 think 參數的模型（例如 Sakura）
            self._supports_think = False
            body.pop("think", None)
            r = self.client.post(f"{self.base_url}/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]["content"]
