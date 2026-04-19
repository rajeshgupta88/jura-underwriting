from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


_CONFIG_PATH = Path(__file__).parent.parent / "config" / "llm_config.yaml"
_config: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    global _config
    if _config is None:
        with open(_CONFIG_PATH) as f:
            _config = yaml.safe_load(f)
    return _config


def get_provider() -> str:
    return _load_config()["provider"]


def get_provider_config() -> dict[str, Any]:
    cfg = _load_config()
    return cfg[cfg["provider"]]


def get_client():
    provider = get_provider()
    pcfg = get_provider_config()
    if provider == "openai":
        from openai import OpenAI
        return OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    elif provider == "anthropic":
        from anthropic import Anthropic
        return Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    raise ValueError(f"Unknown provider: {provider}")


def call_llm(client, system_prompt: str, user_prompt: str) -> str:
    provider = get_provider()
    pcfg = get_provider_config()
    if provider == "openai":
        response = client.chat.completions.create(
            model=pcfg["model"],
            temperature=pcfg["temperature"],
            max_tokens=pcfg["max_tokens"],
            timeout=pcfg["timeout_seconds"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or ""
    elif provider == "anthropic":
        response = client.messages.create(
            model=pcfg["model"],
            temperature=pcfg["temperature"],
            max_tokens=pcfg["max_tokens"],
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return response.content[0].text
    raise ValueError(f"Unknown provider: {provider}")


def llm_status() -> dict[str, Any]:
    provider = get_provider()
    pcfg = get_provider_config()
    key_env_var = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    return {
        "provider": provider,
        "model": pcfg["model"],
        "api_key_set": bool(os.environ.get(key_env_var, "")),
        "key_env_var": key_env_var,
    }
