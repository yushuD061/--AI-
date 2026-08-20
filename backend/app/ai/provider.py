from __future__ import annotations

import time

import httpx

from .config import get_config
from .models import FactSet


def test_connection() -> tuple[int, str]:
    config = get_config()
    if not config.api_key or not config.model:
        raise RuntimeError("尚未配置 API Key 或模型")
    started = time.perf_counter()
    with httpx.Client(timeout=config.timeout_seconds) as client:
        response = client.post(f"{config.base_url}/chat/completions", headers={"Authorization": f"Bearer {config.api_key}"}, json={"model": config.model, "messages": [{"role": "user", "content": "请回复：连接成功"}], "temperature": 0})
        response.raise_for_status()
    return round((time.perf_counter() - started) * 1000), "连接成功"


def generate_answer(question: str, facts: FactSet) -> str:
    config = get_config()
    if not config.api_key or not config.model:
        raise RuntimeError("LLM 未配置")
    with httpx.Client(timeout=config.timeout_seconds) as client:
        response = client.post(f"{config.base_url}/chat/completions", headers={"Authorization": f"Bearer {config.api_key}"}, json={"model": config.model, "temperature": 0, "messages": [{"role": "system", "content": "只根据用户提供的 facts 回答，不得计算或编造数字。"}, {"role": "user", "content": f"问题：{question}\nfacts：{facts.model_dump_json()}"}]})
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
