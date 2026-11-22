"""Register the reference Uni Robot Agent as an external episode evaluator."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from evoab.agents.contracts import AgentLaunch, EvaluatorPlan, LaunchIntegerOption
from evoab.provider.http_presets import resolve_http_preset, validate_http_provider_url


# Settings shared by both harnesses. Service deployment settings stay in EvoAB.
_WORKER_OPTIONS = (
    "integration", "task", "run_id", "env_url", "instruction", "model", "seed",
    "tool_profile", "tool_return_mode", "resource_scheduling", "execution_mode",
    "observation_profile", "skill_writing", "tool_debug", "tools",
    "include_extra_task_requirements", "no_sam3", "sam3_url",
    "max_turns", "episode_timeout_s", "reasoning_effort", "provider_url", "provider_profile",
    "provider_timeout_s", "provider_retries", "provider_retry_backoff_s", "model_parallel_tool_calls",
    "context_policy", "context_observation_horizon", "context_image_token_allowance",
    "context_max_input_tokens", "context_target_input_tokens", "context_keep_recent_blocks",
    "context_keep_observation_blocks", "context_min_recent_tool_images", "context_image_eviction_batch_size",
    "telemetry", "log_level", "save_exchanges",
)


def preset_connection(name: str) -> tuple[str, str, str, str]:
    """Map a Studio preset to a model and transport supported by RUA's Codex planner."""
    connection = resolve_http_preset(
        name, client_protocol="responses", profiles=("passthrough.responses", "omnirouter.chat_completions"),
    )
    return connection.model, connection.base_url, connection.profile, connection.api_key


class RuaAgent:
    """RUA owns model calls, tool execution and episode finalization in its process."""

    model_protocol = "responses"
    owns_provider = True
    workbench_launch = AgentLaunch(
        label="RUA", modes=("agentic",), integrations=("robodojo",),
        description="Uni Robot Agent 连接 EvoAB 管理的 RoboDojo 环境。",
        cli_flags=("--rua-model-preset",), validate_preset=preset_connection,
        model_help="支持使用 API Key 的标准 Responses 或 Chat Completions 预设。",
        options=(LaunchIntegerOption("max_turns", "--max-turns", "Agent 轮次上限", "留空或填 0 表示不限制轮次。"),),
    )

    @classmethod
    def add_cli_arguments(cls, parser) -> None:
        group = parser.add_argument_group("RUA evaluator")
        group.add_argument("--rua-python", help="Interpreter with uni-robot-agent installed (or EVOAB_RUA_PYTHON)")
        group.add_argument("--rua-model-preset", action="store_true",
                           help="Treat --model as an EvoAB model preset, as Studio does")

    @classmethod
    def resolve_defaults(cls, args) -> None:
        args.rua_python = (getattr(args, "rua_python", None) or os.environ.get("EVOAB_RUA_PYTHON")
                           or sys.executable)
        if args.reasoning_effort is None:
            args.reasoning_effort = "max"

    @classmethod
    def validate_config(cls, args, explicit: set[str]) -> None:
        if args.command != "run" or args.integration != "robodojo" or args.interaction_mode != "tool_calling":
            raise ValueError("--agent rua requires run --integration robodojo with tool_calling interaction")
        if (args.tool_profile not in (None, "direct") or args.action_profile not in (None, "direct")
                or args.action_model_url):
            raise ValueError("--agent rua requires the direct tool profile and no action-model URL")
        if any(flag.startswith(("--vla-", "--no-vla-", "--action-model-", "--actor-", "--no-actor-"))
               for flag in explicit):
            raise ValueError("Actor and reviewer settings require the hybrid tool profile")
        # Preserve the venv path when its interpreter is a symlink to base Python.
        python = Path(os.path.abspath(Path(args.rua_python).expanduser()))
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ValueError(f"--rua-python is not an executable file: {python}")
        args.rua_python = str(python)
        if args.rua_model_preset:
            if "--provider-url" in explicit or "--provider-profile" in explicit:
                raise ValueError("--rua-model-preset supplies its own provider URL and profile")
            model, args.provider_url, args.provider_profile, args._api_key = preset_connection(args.model)
            args._rua_model = model
        from evoab.provider.registry import validate_provider_profile

        args.provider_profile = args.provider_profile or "passthrough.responses"
        validate_provider_profile(args.provider_profile, client_protocol=cls.model_protocol)
        if args.provider_url:
            validate_http_provider_url(args.provider_url)
        elif any(flag.startswith("--provider-") for flag in explicit) or explicit & {
            "--model-parallel-tool-calls", "--no-model-parallel-tool-calls",
        }:
            raise ValueError("RUA provider request options require --provider-url")
        args._rua_explicit = sorted(explicit)

    @classmethod
    def evaluator_plan(cls, args) -> EvaluatorPlan:
        settings = {name: getattr(args, name, None) for name in _WORKER_OPTIONS}
        settings["model"] = getattr(args, "_rua_model", args.model)
        flags = {f"--{name.replace('_', '-')}" for name in _WORKER_OPTIONS}
        flags.update(f"--no-{flag[2:]}" for flag in ("--save-exchanges", "--model-parallel-tool-calls",
                                                     "--include-extra-task-requirements", "--skill-writing"))
        payload = {
            "settings": settings,
            "explicit": sorted(flags.intersection(args._rua_explicit)),
            "api_key": args._api_key,
            "model_preset": args.model if args.rua_model_preset else None,
            "session_dir": args._session_dir,
            "identity": getattr(args, "_episode_identity", None),
            "deployment": getattr(args, "_deployment", None),
        }
        worker = Path(__file__).with_name("worker.py").resolve()
        return EvaluatorPlan((args.rua_python, "-I", "-u", str(worker)), json.dumps(payload).encode())
