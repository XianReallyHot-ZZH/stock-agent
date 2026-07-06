"""Thin LLM client (OpenAI-compatible: 智谱GLM / DeepSeek / OpenAI). Uses requests.

Auto-detects which provider to use by which *_API_KEY is present in the env
(unless LLM_PROVIDER forces one). The LLM is ONLY used to write natural-language
commentary grounded in engine facts; it never produces the action numbers.
"""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)

# OpenAI-compatible providers. base + default model + the env var holding the key.
PROVIDERS = {
    "deepseek": {"base": "https://api.deepseek.com", "model": "deepseek-chat", "key_env": "DEEPSEEK_API_KEY"},
    "glm": {"base": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash", "key_env": "GLM_API_KEY"},
    "openai": {"base": "https://api.openai.com/v1", "model": "gpt-4o-mini", "key_env": "OPENAI_API_KEY"},
}


def _resolve():
    """Return (base_url, model, api_key) or None."""
    forced = os.environ.get("LLM_PROVIDER", "").lower()
    order = [forced] + [p for p in PROVIDERS if p != forced] if forced in PROVIDERS else list(PROVIDERS)
    for name in order:
        p = PROVIDERS.get(name)
        if not p:
            continue
        key = os.environ.get(p["key_env"], "")
        if key:
            model = os.environ.get("LLM_MODEL") or p["model"]
            return p["base"], model, key, name
    return None


def llm_available() -> bool:
    return _resolve() is not None


def provider_name() -> str:
    r = _resolve()
    return r[3] if r else "none"


def chat(prompt: str, system: str = "", max_tokens: int = 4000) -> str | None:
    """Call the LLM. Returns text or None on any failure (caller falls back).

    Default max_tokens is generous because reasoning models (deepseek-v4-pro,
    deepseek-reasoner, …) emit reasoning_content that shares the budget — a small
    cap starves the actual answer (content comes back ''). 4000 leaves room for
    ~3k reasoning + content. Non-reasoning models just stop early, so the high
    cap costs nothing extra (you pay per generated token, not per cap).
    """
    res = _resolve()
    if not res:
        return None
    base, model, key, _name = res
    url = base.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    try:
        r = requests.post(
            url, headers=headers,
            json={"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0.5},
            timeout=40,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:  # noqa: BLE001
        log.warning("LLM call failed (will use template): %s", str(e)[:160])
        return None
