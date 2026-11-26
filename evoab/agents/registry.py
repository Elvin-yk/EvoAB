"""Explicit, lazy agent registration."""
from __future__ import annotations
import importlib
from typing import Any
from .contracts import Agent, AgentLaunch

_AGENT_REGISTRY: dict[str, Any] = {}


def register_agent(name: str):
    """Register an agent class.

    Usage:
        @register_agent("codex")
        class CodexAgent:
            def run(self, ...): ...
    """

    def wrapper(agent_cls):
        _AGENT_REGISTRY[name] = agent_cls
        return agent_cls

    return wrapper


def get_agent_backend(name: str) -> Any:
    """Resolve a registered agent or lazily load a built-in SDK adapter."""
    if name not in _AGENT_REGISTRY:
        path = _BUILTIN_AGENTS.get(name)
        if not path:
            available = ", ".join(sorted(_BUILTIN_AGENTS))
            raise ValueError(f"Unknown agent backend: {name!r}. Available: {available}")
        module_path, cls_name = path.rsplit(":", 1)
        _AGENT_REGISTRY[name] = getattr(importlib.import_module(module_path), cls_name)
    return _AGENT_REGISTRY[name]


# Agent registry

_BUILTIN_AGENTS: dict[str, str] = {
    "claude_code": "evoab.agents.claude_code.agent:ClaudeCodeAgent",
    "codex": "evoab.agents.codex.agent:CodexAgent",
    "opencode": "evoab.agents.opencode.agent:OpenCodeAgent",
    "antigravity": "evoab.agents.antigravity.agent:AntigravityAgent",
    "gemini_cli": "evoab.agents.gemini_cli.agent:GeminiCliAgent",
    "native": "evoab.agents.native.agent:NativeAgent",
    "q0_preview": "evoab.agents.q0_preview:Q0PreviewAgent",
}


def create_agent(backend: str, **kwargs) -> Agent:
    """Create an agent by backend name with lazy loading."""
    return get_agent_backend(backend)(**kwargs)


def list_agent_backends() -> tuple[str, ...]:
    """Return built-in and explicitly registered backends without SDK imports."""
    return tuple(sorted(set(_BUILTIN_AGENTS) | set(_AGENT_REGISTRY)))


def agent_launch(name: str) -> AgentLaunch | None:
    """Return the adapter's optional Workbench configuration."""
    return getattr(get_agent_backend(name), "workbench_launch", None)


def list_agent_launches() -> dict[str, AgentLaunch]:
    """Discover Workbench adapters through the same registry as the run CLI."""
    return {name: launch for name in list_agent_backends() if (launch := agent_launch(name)) is not None}
