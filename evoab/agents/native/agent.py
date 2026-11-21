"""EvoAB's built-in agent (``--agent native``).

``--model`` names a model preset from :mod:`evoab.provider.catalog`, which owns
provider connections, request APIs, retries and usage accounting. Two modes:

``tool_calling``   an agent loop with only EvoAB's robot tools registered, with
                   transcript consolidation for long episodes (run-local storage)
``direct_policy``  each inference is one stateless request that must return an
                   action chunk (see :mod:`evoab.direct.runner`)
"""

from __future__ import annotations

import time
from functools import partial
from pathlib import Path
from typing import Any

from evoab.agents.contracts import AgentCapabilities, AgentResult
from evoab.agents.registry import register_agent


def _consolidator(workspace: Path, registry):
    """Transcript consolidator over run-local storage."""
    from nanobot.agent.memory import Consolidator, MemoryStore
    from nanobot.session.manager import SessionManager
    workspace.mkdir(parents=True, exist_ok=True)
    return Consolidator(store=MemoryStore(workspace), sessions=SessionManager(workspace),
                        build_messages=lambda history, current_message=None, **_: list(history),
                        get_tool_definitions=registry.get_definitions)


def _pair(value: str) -> tuple[int, int]:
    first, _, second = value.partition(":")
    try:
        return int(first), int(second)
    except ValueError as error:
        raise ValueError(f"expected two integers as A:B, got {value!r}") from error


@register_agent("native")
class NativeAgent:
    model_protocol = "native"
    owns_provider = True
    capabilities = AgentCapabilities(tool_event_source="execution")

    @classmethod
    def add_cli_arguments(cls, parser) -> None:
        from evoab.direct.actions import REPRESENTATIONS
        from evoab.direct.prompts import BLOCKS
        group = parser.add_argument_group("Direct policy")
        group.add_argument("--direct-representation", choices=tuple(REPRESENTATIONS), default="abs_eef")
        group.add_argument("--direct-horizon", default="3:15", metavar="MIN:MAX",
                           help="Rows per action chunk")
        group.add_argument("--direct-history", default="15:7", metavar="LENGTH:STRIDE",
                           help="Observation window and sampling stride (always includes the newest)")
        group.add_argument("--direct-prompt-blocks", default=",".join(BLOCKS),
                           help="Comma-separated prompt blocks; robot and timing are required")
        group.add_argument("--direct-prompt-file", type=Path,
                           help="Replace the bundled embodiment description with this text")
        group.add_argument("--direct-step-limits", default="0.02:0.1", metavar="METRES:RADIANS",
                           help="Per-row translation and rotation limits")
        group.add_argument("--direct-repair-attempts", type=int, default=2)
        group.add_argument("--direct-max-calls", type=int, default=0, help="0: until the episode ends")

    @classmethod
    def validate_config(cls, args, explicit: set[str]) -> None:
        from evoab.provider import catalog
        if args.provider_url:
            raise ValueError("--agent native uses configured model presets; omit --provider-url")
        names = {p["name"] for p in catalog.presets()}
        if args.model not in names:
            raise ValueError(f"--model must name a model preset (evoab models); configured: {', '.join(sorted(names)) or 'none'}")
        blocks = [b for b in args.direct_prompt_blocks.split(",") if b]
        if not {"robot", "timing"} <= set(blocks):
            raise ValueError("--direct-prompt-blocks must include robot and timing")
        low, high = _pair(args.direct_horizon)
        if not 1 <= low <= high <= 64:
            raise ValueError("--direct-horizon must satisfy 1 <= MIN <= MAX <= 64")
        length, stride = _pair(args.direct_history)
        if length < 1 or stride < 1:
            raise ValueError("--direct-history values must be positive")

    @classmethod
    def config_options(cls, args) -> dict[str, Any]:
        from evoab.direct.runner import DirectConfig
        low, high = _pair(args.direct_horizon)
        length, stride = _pair(args.direct_history)
        metres, _, radians = args.direct_step_limits.partition(":")
        robot = args.direct_prompt_file.read_text(encoding="utf-8") if args.direct_prompt_file else None
        return {"reasoning_effort": args.reasoning_effort, "direct": DirectConfig(
            representation=args.direct_representation, min_horizon=low, max_horizon=high,
            history_length=length, history_stride=stride,
            blocks=tuple(b for b in args.direct_prompt_blocks.split(",") if b), robot_prompt=robot,
            translation_limit_m=float(metres), rotation_limit_rad=float(radians),
            repair_attempts=args.direct_repair_attempts, max_calls=args.direct_max_calls)}

    @classmethod
    def resolve_defaults(cls, args) -> None:
        return None

    def __init__(self, *, model: str, timeout_s: float | None = None, reasoning_effort: str | None = None,
                 direct=None):
        self.preset = model
        self.timeout_s = timeout_s
        self.reasoning_effort = reasoning_effort
        self.direct_config = direct
        self._policy = None

    def model_runtime(self, profile: str, event_bus):  # pragma: no cover - the catalog owns transport
        raise RuntimeError("the native agent does not use the provider gateway")

    def finalize_without_provider(self, result: AgentResult, session_dir: Path | None) -> None:
        return None

    def cancel(self, reason: str) -> None:
        if self._policy is not None:
            self._policy.cancel()

    def close(self) -> None:
        return None

    def run_direct(self, *, episode, integration: str, instruction: str, session_dir: Path) -> AgentResult:
        from evoab.direct.runner import DirectPolicy
        from evoab.provider.catalog import ModelClient
        started = time.time()
        client = ModelClient(self.preset, reasoning_effort=self.reasoning_effort)
        self._policy = DirectPolicy(episode, client, integration=integration, instruction=instruction,
                                    output_dir=session_dir, config=self.direct_config)
        try:
            outcome = self._policy.run()
        finally:
            client.close()
        return AgentResult(
            success=outcome.error is None, message=f"{outcome.calls} inferences, {outcome.steps} control steps",
            error=outcome.error, stop_reason=outcome.stop_reason, turns_used=outcome.calls,
            provider_error=outcome.error if outcome.stop_reason == "provider_error" else None,
            tool_calls=outcome.calls, steps_used=outcome.steps, elapsed_s=time.time() - started,
            trace_format="direct")

    def run(self, *, prompt, task: str, toolkit, executor, max_turns: int = 50, event_bus=None,
            session_dir: Path | None = None, model_endpoint: str | None = None, code_policy=None) -> AgentResult:
        """Tool calling with only EvoAB's robot tools registered."""

        from nanobot.agent.runner import AgentRunner, AgentRunSpec
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.llm_usage.context import llm_usage_source
        from nanobot.utils.llm_runtime import LLMRuntime

        from evoab.agents.native.tools import ImageInbox, RecordingHook, robot_tools
        from evoab.provider.catalog import ModelClient

        if code_policy is not None:
            raise ValueError("code_as_policy is not available with --agent native yet")
        started = time.time()
        client = ModelClient(self.preset, reasoning_effort=self.reasoning_effort)
        registry = ToolRegistry()
        inbox = ImageInbox()
        for tool in robot_tools(executor, inbox):
            registry.register(tool)
        runtime = LLMRuntime.capture(client._provider, client.model, context_window_tokens=200_000,
                                     model_preset=self.preset).with_generation_overrides(
            reasoning_effort=client.reasoning_effort, max_tokens=client.max_tokens)
        user = [{"type": "text", "text": prompt.user}]
        for image in prompt.images:
            from nanobot.utils.helpers import build_image_content_blocks
            user += build_image_content_blocks(image.data, image.mime_type, image.name or "image", image.name or "image")
        messages = ([{"role": "system", "content": prompt.system}] if prompt.system else []) + [{"role": "user", "content": user}]
        root = Path(session_dir or ".") / "agent"
        hook = RecordingHook(root)
        consolidator = _consolidator(root / "workspace", registry)
        session_key = f"evoab:{root.parent.name}"
        definitions = registry.get_definitions()
        spec = AgentRunSpec(
            initial_messages=messages, tools=registry, runtime=runtime,
            max_iterations=max_turns or 1000, max_tool_result_chars=16_000, hook=hook,
            finalize_on_max_iterations=False, error_message=None, llm_usage_source="api", session_key=session_key,
            injection_callback=inbox.drain,
            # Summarize the accepted transcript when it outgrows the context window.
            consolidate_history=partial(consolidator.summarize_transcript, runtime=runtime, session_key=session_key,
                                        tools=definitions, persist=False),
            consolidate_provider_compaction=partial(consolidator.summarize_provider_compaction, runtime=runtime,
                                                    session_key=session_key, tools=definitions, persist=False))

        async def go():
            with llm_usage_source("api"):
                return await AgentRunner().run(spec)

        try:
            outcome = client._loop.run_until_complete(go())
        finally:
            client.close()
        return AgentResult(
            success=outcome.error is None, message=outcome.final_content or "", error=outcome.error,
            stop_reason=None if outcome.stop_reason == "completed" else outcome.stop_reason,
            turns_used=len(outcome.round_usages), tool_calls=len(outcome.tools_used),
            steps_used=len(outcome.round_usages) + len(outcome.tools_used), elapsed_s=time.time() - started,
            trace_format="native")
