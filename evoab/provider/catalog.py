"""Model providers, presets and usage for EvoAB's built-in agent runtime.

Everything lives under one directory (``EVOAB_AGENT_HOME``, default
``~/.config/evoab/agent``): ``config.json`` holds providers and model presets,
and ``llm_usage.sqlite3`` records every model call. Studio's settings pages and
``evoab models`` edit the same file through the functions below.

The agent runtime is installed with ``pip install 'evoab[agent]'``.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INSTALL_HINT = "the agent runtime is not installed; install it with: pip install 'evoab[agent]'"
_ACTIVE: Path | None = None


class CatalogError(ValueError):
    """A rejected settings change; ``status`` is the matching HTTP status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def home() -> Path:
    return Path(os.environ.get("EVOAB_AGENT_HOME") or Path.home() / ".config" / "evoab" / "agent").expanduser()


def config_path() -> Path:
    return home() / "config.json"


def _activate() -> None:
    """Point the runtime at EvoAB's directory before any config or usage access."""
    global _ACTIVE
    path = config_path()
    if _ACTIVE == path:
        return
    try:
        from nanobot.config.loader import set_config_path
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise RuntimeError(INSTALL_HINT) from error
    path.parent.mkdir(parents=True, exist_ok=True)
    set_config_path(path)
    _ACTIVE = path


def load_config():
    _activate()
    from nanobot.config.loader import load_config as load
    return load(config_path())


def _call(operation, fields: dict[str, Any] | None = None, **kwargs):
    """Run one settings operation with form-style fields; map validation failures."""
    _activate()
    from nanobot.webui.settings_contracts import WebUISettingsError
    query = {key: [value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list))
                   else str(value).lower() if isinstance(value, bool) else str(value)]
             for key, value in (fields or {}).items() if value is not None}
    try:
        result = operation(query, config_path=config_path(), **kwargs)
    except WebUISettingsError as error:
        raise CatalogError(error.message, error.status) from error
    if config_path().exists():
        os.chmod(config_path(), 0o600)
    return result


# -- read ---------------------------------------------------------------------

_PROVIDER_FIELDS = ("name", "label", "is_custom", "configured", "api_base", "default_api_base", "request_apis",
                    "api", "provider_api_configurable", "advanced_fields", "extra_headers", "extra_query",
                    "extra_body", "thinking_style", "use_proxy", "model_catalog", "api_key_hint", "requires_api_key",
                    "requires_api_base", "oauth")
_PRESET_FIELDS = ("name", "label", "model", "provider", "resolved_provider", "max_tokens", "context_window_tokens",
                  "temperature", "reasoning_effort", "reasoning_effort_values", "api", "extra_headers", "extra_query",
                  "extra_body")


def settings() -> dict[str, Any]:
    """Providers (configured first, then addable built-ins) and model presets, without secrets."""
    _activate()
    from nanobot.webui import settings_api
    payload = settings_api.settings_payload(config_path=config_path())
    providers = [{k: p.get(k) for k in _PROVIDER_FIELDS if k in p} for p in payload["providers"]]
    presets = [{k: p.get(k) for k in _PRESET_FIELDS if k in p} for p in payload["model_presets"] if p["name"] != "default"]
    providers.sort(key=lambda p: (not p.get("configured"), not p.get("is_custom"), p.get("label") or p["name"]))
    return {"providers": providers, "presets": presets, "config": str(config_path())}


def providers(config=None) -> list[dict[str, Any]]:
    return [p for p in settings()["providers"] if p.get("configured")]


def presets(config=None) -> list[dict[str, Any]]:
    return settings()["presets"]


def provider_models(provider: str, refresh: bool = False) -> dict[str, Any]:
    from nanobot.webui import settings_api
    return _call(settings_api.provider_models_payload, {"provider": provider, "refresh": "1" if refresh else None})


def usage() -> dict[str, Any]:
    """Daily token and request totals for every recorded model call (last 365 days)."""
    _activate()
    from nanobot.webui import settings_api
    return settings_api.settings_usage_payload(config_path=config_path())


# -- write --------------------------------------------------------------------

def save_preset(fields: dict[str, Any], *, existing: str | None = None) -> dict[str, Any]:
    from nanobot.webui import settings_api
    if existing:
        _call(settings_api.update_model_configuration, {**fields, "name": existing,
                                                         "new_name": fields.get("name") or existing})
    else:
        _call(settings_api.create_model_configuration, fields)
    return settings()


def delete_preset(name: str) -> dict[str, Any]:
    from nanobot.webui import settings_api
    _call(settings_api.delete_model_configuration, {"name": name})
    return settings()


def save_provider(fields: dict[str, Any], *, existing: str | None = None) -> dict[str, Any]:
    from nanobot.webui import settings_api
    if existing:
        _call(settings_api.update_provider_settings, {**fields, "provider": existing})
    else:
        _call(settings_api.create_provider_settings, fields)
    return settings()


def delete_provider(name: str) -> dict[str, Any]:
    """Remove a custom provider that no preset uses."""
    from nanobot.config.loader import save_config
    config = load_config()
    extra = config.providers.model_extra or {}
    if name not in extra:
        raise CatalogError("only custom providers can be removed", 404 if name not in dict(config.providers) else 400)
    users = [n for n, p in (config.model_presets or {}).items() if p.provider == name]
    if users:
        raise CatalogError(f"provider is used by presets: {', '.join(users)}", 409)
    delattr(config.providers, name)
    save_config(config, config_path())
    os.chmod(config_path(), 0o600)
    return settings()


# -- calls --------------------------------------------------------------------

@dataclass(frozen=True)
class Completion:
    """One model call as EvoAB records it."""

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
    """A provider bound to one preset; synchronous for EvoAB's control loops."""

    def __init__(self, preset: str, *, reasoning_effort: str | None = None, max_tokens: int | None = None):
        config = load_config()
        resolved = (config.model_presets or {}).get(preset)
        if resolved is None:
            known = ", ".join(sorted(config.model_presets or {})) or "none"
            raise ValueError(f"unknown model preset {preset!r} (configured: {known})")
        from nanobot.llm_usage.service import record_llm_call
        from nanobot.providers.factory import make_provider
        self.preset = preset
        self.model = resolved.model
        self.provider_name = resolved.provider
        self.reasoning_effort = reasoning_effort if reasoning_effort is not None else resolved.reasoning_effort
        self.max_tokens = max_tokens or resolved.max_tokens
        self.temperature = resolved.temperature
        self.context_window_tokens = resolved.context_window_tokens
        self._provider = make_provider(config, preset_name=preset)
        self._provider.set_llm_call_observer(record_llm_call)  # every physical call lands in the usage store
        self._loop = asyncio.new_event_loop()

    def describe(self) -> dict[str, Any]:
        return {"preset": self.preset, "model": self.model, "provider": self.provider_name,
                "reasoning_effort": self.reasoning_effort, "max_tokens": self.max_tokens,
                "temperature": self.temperature}

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
        error = (response.content or "provider error") if response.finish_reason == "error" else None
        return Completion(
            content=response.content or "", reasoning=response.reasoning_content or "",
            tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments} for c in response.tool_calls],
            finish_reason=response.finish_reason, usage=_usage(response.usage),
            started_at=started, latency_s=latency, error=error)

    def close(self) -> None:
        if not self._loop.is_closed():
            self._loop.close()
