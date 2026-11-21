"""Model access through nanobot.

nanobot (installed from PyPI, ``pip install 'evoab[nanobot]'``) owns provider
connections, model presets, request APIs, retries and usage accounting. EvoAB
reads the same configuration file nanobot's own WebUI edits, so a provider or
preset added there is immediately usable here, and every EvoAB model call is
recorded in nanobot's usage store next to its other calls.

``EVOAB_NANOBOT_CONFIG`` selects a config file other than nanobot's default.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INSTALL_HINT = "nanobot is not installed; install it with: pip install 'evoab[nanobot]'"


def _require():
    try:
        import nanobot  # noqa: F401
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise RuntimeError(INSTALL_HINT) from error


def config_path() -> Path:
    if value := os.environ.get("EVOAB_NANOBOT_CONFIG"):
        return Path(value).expanduser()
    _require()
    from nanobot.config.loader import get_config_path
    return Path(get_config_path())


def load_config():
    _require()
    from nanobot.config.loader import load_config as load
    return load(config_path())


def _configured(provider) -> bool:
    return bool(getattr(provider, "api_key", None) or getattr(provider, "api_base", None))


def providers(config=None) -> list[dict[str, Any]]:
    """Providers with an endpoint or key, without secrets."""
    config = config or load_config()
    items = []
    for name, provider in config.providers:
        if provider is None or not _configured(provider):
            continue
        api = getattr(provider, "api", None)
        items.append({
            "name": name,
            "label": getattr(provider, "display_name", None) or name,
            "api_base": getattr(provider, "api_base", None),
            "has_key": bool(getattr(provider, "api_key", None)),
            "apis": list(api.supported_apis) if api else [],
        })
    return items


def presets(config=None) -> list[dict[str, Any]]:
    """Named model presets: the unit EvoAB runs select."""
    config = config or load_config()
    return [{"name": name, "model": preset.model, "provider": preset.provider,
             "reasoning_effort": preset.reasoning_effort, "max_tokens": preset.max_tokens,
             "temperature": preset.temperature, "context_window_tokens": preset.context_window_tokens}
            for name, preset in (config.model_presets or {}).items()]


@dataclass(frozen=True)
class Completion:
    """One provider call as EvoAB records it."""

    content: str
    reasoning: str
    tool_calls: list[dict[str, Any]]
    finish_reason: str
    usage: dict[str, Any]
    started_at: float
    latency_s: float
    error: str | None = None


def _usage(usage) -> dict[str, Any]:
    if usage is None:
        return {}
    return {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens, "cached_input_tokens": usage.cache_read_tokens,
            "estimated": usage.estimated_tokens > 0}


class ModelClient:
    """A nanobot provider bound to one preset; synchronous for EvoAB's control loops."""

    def __init__(self, preset: str, *, reasoning_effort: str | None = None, max_tokens: int | None = None):
        config = load_config()
        resolved = (config.model_presets or {}).get(preset)
        if resolved is None:
            known = ", ".join(sorted(config.model_presets or {})) or "none"
            raise ValueError(f"unknown nanobot model preset {preset!r} (configured: {known})")
        from nanobot.providers.factory import make_provider
        self.preset = preset
        self.model = resolved.model
        self.provider_name = resolved.provider
        self.reasoning_effort = reasoning_effort if reasoning_effort is not None else resolved.reasoning_effort
        self.max_tokens = max_tokens or resolved.max_tokens
        self.temperature = resolved.temperature
        self._provider = make_provider(config, preset_name=preset)
        # Record every physical call in nanobot's usage store, as nanobot's own gateway does.
        from nanobot.llm_usage.service import record_llm_call
        self._provider.set_llm_call_observer(record_llm_call)
        self._loop = asyncio.new_event_loop()

    def describe(self) -> dict[str, Any]:
        return {"preset": self.preset, "model": self.model, "provider": self.provider_name,
                "reasoning_effort": self.reasoning_effort, "max_tokens": self.max_tokens,
                "temperature": self.temperature, "config": str(config_path())}

    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None,
                 tool_choice: str | dict[str, Any] | None = None) -> Completion:
        from nanobot.llm_usage.context import llm_usage_source

        async def call():
            with llm_usage_source("api"):
                return await self._provider.chat_with_retry(
                    messages=messages, tools=tools, model=self.model, max_tokens=self.max_tokens,
                    temperature=self.temperature, reasoning_effort=self.reasoning_effort,
                    tool_choice=tool_choice)

        started, clock = time.time(), time.perf_counter()
        response = self._loop.run_until_complete(call())
        latency = time.perf_counter() - clock
        error = None
        if response.finish_reason == "error":
            error = response.content or "provider error"
        return Completion(
            content=response.content or "", reasoning=response.reasoning_content or "",
            tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in response.tool_calls],
            finish_reason=response.finish_reason, usage=_usage(response.usage),
            started_at=started, latency_s=latency, error=error)

    def close(self) -> None:
        if not self._loop.is_closed():
            self._loop.close()
