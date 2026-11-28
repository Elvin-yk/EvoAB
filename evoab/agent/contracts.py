"""The agent's result and Studio launch settings."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LaunchIntegerOption:
    """Optional integer that Studio can forward to a registered CLI flag."""

    name: str
    flag: str
    label: str
    help: str
    minimum: int = 0


@dataclass(frozen=True)
class AgentLaunch:
    """Benchmark launch capabilities declared by an agent adapter."""

    label: str
    description: str = ""
    integrations: tuple[str, ...] | None = None
    cli_flags: tuple[str, ...] = ()
    options: tuple[LaunchIntegerOption, ...] = ()
    validate_preset: Callable[[str], object] | None = None
    model_help: str = ""
    probe_preset: Callable[[str, str], dict[str, Any]] | None = None


@dataclass
class AgentResult:
    """Final result from one agent run() call."""

    success: bool = False
    message: str = ""
    error: str | None = None
    provider_error: str | None = None
    cleanup_error: str | None = None
    stop_reason: str | None = None
    turns_used: int = 0
    tool_calls: int = 0
    # SDK progress count: assistant messages plus tool invocations, not physical actions.
    steps_used: int = 0
    elapsed_s: float = 0.0
    provider_log_path: str | None = None
    trace_format: str | None = None
    traceback: str | None = None


class AgentInterrupted(KeyboardInterrupt):
    """Preserve a stopped agent's result while propagating interruption to its caller."""

    def __init__(self, result: AgentResult):
        super().__init__(result.error or "Agent interrupted")
        self.result = result


