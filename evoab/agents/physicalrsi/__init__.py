"""PhysicalRSI System 1 as a single-task EvoAB agent."""

from __future__ import annotations

import json
import time
from collections import deque
from importlib.util import find_spec
from pathlib import Path
from threading import Event

from evoab.agents.contracts import (
    AgentCapabilities, AgentInterrupted, AgentLaunch, AgentResult, LaunchIntegerOption,
)
from evoab.agents.registry import register_agent
from evoab.tools.client import ToolClient

SIMULATORS = ("robodojo", "libero", "libero_pro", "libero_plus", "robotwin", "calvin", "metaworld")


def _validate_preset(name: str) -> None:
    from evoab.provider import catalog

    if find_spec("physicalrsi") is None:
        raise ValueError("Install the PhysicalRSI extra with pip install 'evoab[agent,physicalrsi]'.")
    if name not in {preset["name"] for preset in catalog.presets()}:
        raise ValueError(f"Unknown model preset {name!r}")


@register_agent("physicalrsi")
class PhysicalRSIAgent:
    model_protocol = "native"
    owns_provider = True
    capabilities = AgentCapabilities(tool_event_source="execution")
    workbench_launch = AgentLaunch(
        label="PhysicalRSI",
        modes=("agentic",),
        description="使用 PhysicalRSI 的技能组合选择并执行机器人工具。",
        integrations=SIMULATORS,
        options=(LaunchIntegerOption("max_turns", "--max-turns", "决策轮数上限",
                                     "留空使用默认值，0 表示不限。"),),
        validate_preset=_validate_preset,
        model_help="使用工作台已有模型预设。运行需要安装 PhysicalRSI 依赖。",
    )

    @classmethod
    def add_cli_arguments(cls, parser) -> None:
        pass

    @classmethod
    def resolve_defaults(cls, args) -> None:
        pass

    @classmethod
    def validate_config(cls, args, explicit: set[str]) -> None:
        if args.integration not in SIMULATORS or args.interaction_mode != "tool_calling":
            raise ValueError("PhysicalRSI requires tool calling in a supported simulator")
        if args.provider_url:
            raise ValueError("PhysicalRSI uses model presets. Omit --provider-url.")
        _validate_preset(args.model)

    @classmethod
    def config_options(cls, args) -> dict:
        return {"reasoning_effort": args.reasoning_effort}

    def __init__(self, *, model: str, timeout_s: float | None = None,
                 reasoning_effort: str | None = None):
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout_s = timeout_s
        self.cancelled = Event()

    def cancel(self, reason: str) -> None:
        self.cancelled.set()

    def close(self) -> None:
        self.cancel("closed")

    def finalize_without_provider(self, result: AgentResult, session_dir: Path | None) -> None:
        pass

    def run(self, *, prompt, task: str, toolkit, executor, max_turns: int = 50,
            event_bus=None, session_dir: Path | None = None, model_endpoint=None,
            code_policy=None) -> AgentResult:
        from nanobot.utils.helpers import build_image_content_blocks
        from physicalrsi import CallableSkill, SkillDescriptor, compose_sequence
        from PhysicalRSI_core.contracts import Cancelled, Context
        from PhysicalRSI_core.infra.storage import file_digest

        from evoab.agents.native.tools import _scrub
        from evoab.provider.catalog import ModelClient

        if code_policy is not None:
            raise ValueError("PhysicalRSI requires tool calling")

        def images(attachments):
            return [block for image in attachments for block in build_image_content_blocks(
                image.data, image.mime_type, image.name or "camera", image.name or "camera"
            )]

        started = time.monotonic()
        deadline = started + self.timeout_s if self.timeout_s else None
        context = Context("evoab-physicalrsi", cancelled=self.cancelled, deadline=deadline)
        tools = ToolClient(executor, context.check)
        declarations = tools.list()
        names = {spec["name"] for spec in declarations}
        definitions = [{"type": "function", "function": {
            "name": spec["name"], "description": spec.get("description", ""),
            "parameters": spec.get("input_schema") or spec.get("parameters") or {"type": "object"},
        }} for spec in declarations]
        initial = [
            {"role": "system", "content": "\n\n".join(filter(None, (
                prompt.system,
                "Choose one registered tool per turn and inspect its feedback before continuing.",
            )))},
            {"role": "user", "content": [{"type": "text", "text": prompt.user or task},
                                         *images(prompt.images)]},
        ]
        recent = deque(maxlen=3)
        observed = [{"role": message["role"], "content": _scrub(message["content"])}
                    for message in initial]
        result = AgentResult(trace_format="native")
        round_path = Path(session_dir) / "agent" / "rounds.jsonl" if session_dir else None
        if round_path is not None:
            round_path.parent.mkdir(parents=True, exist_ok=True)

        def prepare(history, ctx):
            ctx.check()
            return initial + [message for turn in history for message in turn]

        def decide(messages, ctx):
            ctx.check()
            reply = client.complete(messages, tools=definitions or None,
                                    tool_choice="required" if definitions else None)
            ctx.check()
            return reply

        identity = {"implementation": file_digest(Path(__file__))}
        skill = compose_sequence(
            "physicalrsi-system1",
            CallableSkill(SkillDescriptor("prepare-observations", "1", {"name": "history"},
                                          {"name": "messages"}), prepare,
                          executable_identity=identity),
            CallableSkill(SkillDescriptor("select-tool", "1", {"name": "messages"},
                                          {"name": "decision"}), decide,
                          executable_identity=identity),
        )
        client = ModelClient(self.model, reasoning_effort=self.reasoning_effort)
        try:
            while not max_turns or result.turns_used < max_turns:
                reply = skill.execute(recent, context)
                result.turns_used += 1
                result.message = reply.content
                calls = reply.tool_calls
                action_calls = []
                try:
                    if reply.error:
                        result.provider_error = reply.error
                        result.error = reply.error
                        result.stop_reason = "provider_error"
                        break
                    if not calls:
                        break
                    if len(calls) != 1:
                        result.error = "PhysicalRSI requires exactly one tool call per turn"
                        result.stop_reason = "invalid_tool_call"
                        break
                    call = calls[0]
                    name = call["name"]
                    try:
                        arguments = call["arguments"]
                        if isinstance(arguments, str):
                            arguments = json.loads(arguments)
                        if not isinstance(arguments, dict) or name not in names:
                            raise ValueError("Tool call must name a visible tool and supply an argument object")
                    except ValueError as error:
                        result.error = str(error)
                        result.stop_reason = "invalid_tool_call"
                        break
                    action_calls = [{**call, "arguments": arguments}]
                    result.tool_calls += 1
                    output = tools.call(name, arguments)
                    if output.action and (output.action.executed == "unknown" or output.action.operator_required):
                        result.error = "Tool execution requires reconciliation before continuing"
                        result.stop_reason = "uncertain_execution"
                        break
                    if name == "finish" and output.success and not output.error:
                        break
                    state = output.extra.get("state") or output.obs or {}
                    if isinstance(state, dict) and state.get("ended"):
                        result.stop_reason = "episode_ended"
                        break
                    incoming = [{"role": "user", "content": [
                        {"type": "text", "text": f"Tool {name}({json.dumps(arguments, ensure_ascii=False)}) returned:\n"
                                                 + output.model_json()},
                        *images(output.images),
                    ]}]
                    recent.append(incoming)
                finally:
                    if round_path is not None:
                        record = {
                            "iteration": result.turns_used,
                            "started_at": reply.started_at, "finished_at": time.time(),
                            "input": observed,
                            "content": reply.content, "reasoning": reply.reasoning,
                            "tool_calls": action_calls or calls,
                            "usage": reply.usage, "finish_reason": reply.finish_reason,
                            "stop_reason": result.stop_reason, "error": result.error,
                        }
                        with round_path.open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                observed = [{"role": message["role"], "content": _scrub(message["content"])}
                            for message in incoming]
            else:
                result.stop_reason = "max_turns"
            result.success = result.error is None
            return result
        except (KeyboardInterrupt, Cancelled) as error:
            result.error = "PhysicalRSI execution interrupted"
            result.stop_reason = "interrupted"
            raise AgentInterrupted(result) from error
        finally:
            result.steps_used = result.turns_used + result.tool_calls
            result.elapsed_s = time.monotonic() - started
            client.close()
