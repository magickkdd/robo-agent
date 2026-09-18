from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Dict, List, Protocol


class ModelAdapter(Protocol):
    def complete(self, messages: List[Dict[str, str]]) -> str: ...


class DeepSeekAdapter:
    """OpenAI-compatible DeepSeek adapter; never used by the offline MVP demo."""

    def __init__(self, model: str = "deepseek-chat", endpoint: str | None = None, timeout: float = 60.0) -> None:
        self.model = model
        self.endpoint = endpoint or os.getenv("DEEPSEEK_ENDPOINT", "https://api.deepseek.com/chat/completions")
        self.timeout = timeout

    def complete(self, messages: List[Dict[str, str]]) -> str:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        payload = json.dumps({"model": self.model, "messages": messages, "temperature": 0}).encode()
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body: Dict[str, Any] = json.loads(response.read().decode("utf-8"))
        return body["choices"][0]["message"]["content"]

