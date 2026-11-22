"""Agent protocol, results, and registry factories."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from evoab.context.prompt import Prompt
from evoab.events import EventBus
from evoab.execution import ToolExecutor
from evoab.tools.toolkit import Toolkit

@dataclass(frozen=True)
class CodePolicyContext:
    """Native process setup and trusted completion for program-driven runs."""

    cwd: str
    env: dict[str, str]
    finish_request: Callable[[], dict | None]
    revoke: Callable[[], None]


@dataclass(frozen=True)
class AgentCapabilities:
    """Agent capabilities used to select the canonical tool-event source."""

    tool_event_source: Literal["agent", "execution"] = "execution"


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
    modes: tuple[Literal["direct_policy", "agentic", "agent_policy"], ...]
    description: str = ""
    integrations: tuple[str, ...] | None = None
    cli_flags: tuple[str, ...] = ()
    options: tuple[LaunchIntegerOption, ...] = ()
    validate_preset: Callable[[str], object] | None = None
    model_help: str = ""


@dataclass(frozen=True)
class EvaluatorPlan:
    """Host process for an agent that owns its own prepared-episode client."""

    command: tuple[str, ...]
    input_data: bytes


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
    train_path: str | None = None
    train_partial: bool = False
    train_stop_reason: str | None = None
    train_error: str | None = None
    main_thread_id: str = ""
    trace_format: str | None = None
    traceback: str | None = None
    # Diagnostic trajectory retained after strict SFT validation fails; not necessarily trainable.
    train_debug_path: str | None = None


class AgentInterrupted(KeyboardInterrupt):
    """Preserve a stopped agent's result while propagating interruption to its caller."""

    def __init__(self, result: AgentResult):
        super().__init__(result.error or "Agent interrupted")
        self.result = result


class Agent(Protocol):
    """Protocol implemented by all agent backends; max_turns=0 disables the turn cap."""

    model_protocol: str
    capabilities: AgentCapabilities

    @classmethod
    def add_cli_arguments(cls, parser) -> None: ...

    @classmethod
    def validate_config(cls, args, explicit: set[str]) -> None: ...

    @classmethod
    def config_options(cls, args) -> dict[str, Any]: ...

    @classmethod
    def resolve_defaults(cls, args) -> None: ...

    def model_runtime(self, profile: str, event_bus: EventBus | None): ...

    def finalize_without_provider(self, result: AgentResult, session_dir: Path | None) -> None: ...

    def cancel(self, reason: str) -> None: ...

    def close(self) -> None: ...

    def run(
        self,
        *,
        prompt: Prompt,
        task: str,
        toolkit: Toolkit,
        executor: ToolExecutor,
        max_turns: int = 50,
        event_bus: EventBus | None = None,
        session_dir: Path | None = None,
        model_endpoint: str | None = None,
        code_policy: CodePolicyContext | None = None,
    ) -> AgentResult: ...
