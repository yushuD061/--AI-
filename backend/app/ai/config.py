from __future__ import annotations

import os
from dataclasses import dataclass, replace
from urllib.parse import urlparse


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    base_url: str
    api_key: str
    timeout_seconds: int
    source: str = "environment"

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def api_key_masked(self) -> str | None:
        if not self.api_key:
            return None
        return f"{self.api_key[:3]}****{self.api_key[-4:]}"


def _from_env() -> LLMConfig:
    try:
        timeout = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    except ValueError:
        timeout = 30
    return LLMConfig(
        provider=os.getenv("LLM_PROVIDER", "deepseek"),
        model=os.getenv("LLM_MODEL", ""),
        base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("LLM_API_KEY", ""),
        timeout_seconds=max(5, min(timeout, 120)),
    )


_config = _from_env()


def get_config() -> LLMConfig:
    return _config


def public_config() -> dict:
    current = get_config()
    return {
        "provider": current.provider,
        "model": current.model,
        "base_url": current.base_url,
        "has_api_key": current.has_api_key,
        "api_key_masked": current.api_key_masked,
        "timeout_seconds": current.timeout_seconds,
        "source": current.source,
    }


def update_config(provider: str, model: str, base_url: str, timeout_seconds: int) -> dict:
    global _config
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base_url 必须是有效的 http 或 https 地址")
    if not model.strip():
        raise ValueError("model 不能为空")
    if not 5 <= timeout_seconds <= 120:
        raise ValueError("timeout_seconds 必须在 5 到 120 之间")
    _config = replace(_config, provider=provider.strip(), model=model.strip(), base_url=base_url.rstrip("/"), timeout_seconds=timeout_seconds, source="runtime")
    return public_config()
