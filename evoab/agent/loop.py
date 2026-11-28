"""EvoAB's built-in agent, the only agent EvoAB runs.

``--model`` names a model preset from :mod:`evoab.agent.models.catalog`, which owns
provider connections, request APIs, retries and usage accounting. Two loops:

``run``         an agent session over the role's calls: tools, ``run_program``
                (act: code) and the action-chunk tool (act: action next to tools
                or code), with transcript consolidation for long episodes
``run_direct``  act: [action] alone: each inference is one stateless request that
                must return an action chunk (see :mod:`evoab.agent.act.action.runner`)
"""

from __future__ import annotations

import time
from functools import partial
from pathlib import Path
from typing import Any

from evoab.agent.contracts import AgentLaunch, AgentResult
from evoab.agent.models.probe import probe_native_preset


def _consolidator(workspace: Path, registry):
    """Transcript consolidator over run-local storage."""
    from nanobot.agent.memory import Consolidator, MemoryStore
    from nanobot.session.manager import SessionManager
    workspace.mkdir(parents=True, exist_ok=True)
    return Consolidator(store=MemoryStore(workspace), sessions=SessionManager(workspace),
                        build_messages=lambda history, current_message=None, **_: list(history),
                        get_tool_definitions=registry.get_definitions)


class NativeAgent:
    workbench_launch = AgentLaunch(
        label="EvoAB 内置 Agent",
        description="使用 EvoAB 内置运行时与模型预设。",
        probe_preset=probe_native_preset,
    )

    @classmethod
    def validate_config(cls, args, explicit: set[str]) -> None:
        from evoab.agent.models import catalog
        names = {p["name"] for p in catalog.presets()}
        if args.model not in names:
            raise ValueError(f"--model must name a model preset (evoab models); configured: {', '.join(sorted(names)) or 'none'}")

    @classmethod
    def config_options(cls, args) -> dict[str, Any]:
        return {"reasoning_effort": args.reasoning_effort, "direct": getattr(args, "direct_config", None)}

    def __init__(self, *, model: str, timeout_s: float | None = None, reasoning_effort: str | None = None,
                 direct=None):
        self.preset = model
        self.timeout_s = timeout_s
        self.reasoning_effort = reasoning_effort
        self.direct_config = direct
        self._policy = None

    def cancel(self, reason: str) -> None:
        if self._policy is not None:
            self._policy.cancel()

    def close(self) -> None:
        return None

    def run_direct(self, *, episode, integration: str, instruction: str, session_dir: Path) -> AgentResult:
        from evoab.agent.act.action.runner import DirectPolicy
        from evoab.agent.models.catalog import ModelClient
        started = time.time()
        client = ModelClient(self.preset, reasoning_effort=self.reasoning_effort)
        self._policy = DirectPolicy(episode, client, integration=integration, instruction=instruction,
                                    output_dir=session_dir, config=self.direct_config)
        if self.timeout_s:
            self._policy.deadline = time.monotonic() + self.timeout_s
        try:
            outcome = self._policy.run()
        finally:
            client.close()
        return AgentResult(
            success=outcome.error is None and outcome.stop_reason not in {"timeout", "cancelled"},
            message=f"{outcome.calls} inferences, {outcome.steps} control steps",
            error=outcome.error, stop_reason=outcome.stop_reason, turns_used=outcome.calls,
            provider_error=outcome.error if outcome.stop_reason == "provider_error" else None,
            tool_calls=outcome.calls, steps_used=outcome.steps, elapsed_s=time.time() - started,
            trace_format="direct")

    def run(self, *, prompt, task: str, toolkit, executor, max_turns: int = 50, event_bus=None,
            session_dir: Path | None = None) -> AgentResult:
        """Tool calling with only EvoAB's robot tools registered."""

        from nanobot.agent.runner import AgentRunner, AgentRunSpec
        from nanobot.agent.tools.registry import ToolRegistry
        from nanobot.llm_usage.context import llm_usage_source
        from nanobot.utils.llm_runtime import LLMRuntime

        from evoab.agent.calls import ImageInbox, RecordingHook, robot_tools
        from evoab.agent.models.catalog import ModelClient

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


def agent_launch(name: str) -> AgentLaunch | None:
    """Studio's launch settings for the built-in agent; there is no other agent."""
    return NativeAgent.workbench_launch if name == "native" else None


def list_agent_launches() -> dict[str, AgentLaunch]:
    return {"native": NativeAgent.workbench_launch}
