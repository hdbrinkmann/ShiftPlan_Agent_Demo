import os
import httpx
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

_raw_base = os.getenv("SCW_BASE_URL", "https://api.scaleway.ai/v1").replace("\"", "")
if _raw_base.startswith("ttps://"):
    _raw_base = "h" + _raw_base
if not _raw_base.startswith("http"):
    _raw_base = "https://" + _raw_base.lstrip(":/")
SCW_BASE_URL = _raw_base
SCW_ACCESS_KEY = os.getenv("SCW_ACCESS_KEY")
SCW_SECRET_KEY = os.getenv("SCW_SECRET_KEY")
SCW_ORG = os.getenv("SCW_DEFAULT_ORGANIZATION_ID")
SCW_PROJECT = os.getenv("SCW_DEFAULT_PROJECT_ID")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

class ScalewayLLM:
    def __init__(self, base_url: str = SCW_BASE_URL, model: str = LLM_MODEL,
                 access_key: Optional[str] = SCW_ACCESS_KEY, secret_key: Optional[str] = SCW_SECRET_KEY):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.access_key = access_key
        self.secret_key = secret_key
        self._client = httpx.Client(timeout=5)
        # offline/disabled if no token present
        self.enabled = bool(self.secret_key or self.access_key) and (os.getenv("SHIFTPLAN_OFFLINE", "0") != "1")

    def _headers(self) -> dict:
        headers = {
            "Content-Type": "application/json",
        }
        # Scaleway AI often proxies OpenAI-compatible endpoints with Bearer token composed from keys
        # We avoid logging credentials and only set header
        token = None
        if self.secret_key:
            token = self.secret_key
        elif self.access_key:
            token = self.access_key
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        if not self.enabled:
            # Fallback local summary
            return f"{user_prompt[:120]}"
        # Try OpenAI compatible: /chat/completions
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 256,
        }
        try:
            r = self._client.post(url, json=payload, headers=self._headers())
            r.raise_for_status()
            data = r.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content") or str(data)
        except Exception:
            # Graceful degradation: return the user prompt truncated
            return f"{user_prompt[:160]}"
