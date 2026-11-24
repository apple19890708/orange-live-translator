"""OpenAI 相容 API（OpenRouter、LM Studio、llama.cpp server 等）。預設不使用，留作備選。"""
from __future__ import annotations

import os

import httpx

from .base import Translator


class OpenAICompatTranslator(Translator):
    def __init__(self, cfg, glossary):
        super().__init__(cfg, glossary)
        self.base_url = (cfg.get("base_url") or "https://openrouter.ai/api/v1").rstrip("/")
        self.model = cfg["model"]
        key_env = cfg.get("api_key_env") or ""
        self.api_key = os.environ.get(key_env, "") if key_env else ""
        self.client = httpx.Client(timeout=httpx.Timeout(60.0, connect=5.0))

    def chat(self, system: str, user: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        r = self.client.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": float(self.cfg.get("temperature", 0.2)),
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
