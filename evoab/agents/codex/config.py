"""Codex model settings, runtime defaults and executable validation."""
from __future__ import annotations
from evoab.agents.contracts import AgentLaunch
from evoab.provider.http_presets import preset_for_protocol
from typing import Any
import os
from pathlib import Path
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")

def validate_agent_python(value: str | None) -> str | None:
    """Keep the venv path intact while validating the requested executable."""
    if value is None:
        return None
    if not value.strip():
        raise ValueError("--agent-python must name an executable Python interpreter")
    # Resolving symlinks would turn a venv interpreter into its base interpreter.
    path = Path(os.path.abspath(Path(value).expanduser()))
    if not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError(f"--agent-python is not an executable file: {path}")
    return str(path)


class CodexConfig:
    model_protocol = "responses"
    # Channels this runtime can drive (see evoab/composition).
    # Runs code-channel programs in its own terminal instead of the run_program tool.
    runs_programs = True
    workbench_launch = AgentLaunch(
        label="Codex", description="OpenAI Codex SDK：原生 shell、文件编辑与 MCP 工具，支持工具调用与代码通道。", cli_flags=("--model-preset",),
        validate_preset=lambda name: preset_for_protocol(name, "responses"),
        model_help="使用 API Key 的模型预设；需要提供方支持 responses 或可转换的接口。",
    )

    @classmethod
    def resolve_defaults(cls, args):
        if args.reasoning_effort is None:
            args.reasoning_effort = "max"
        if args.mcp_timeout_s is None:
            args.mcp_timeout_s = 10800

    @classmethod
    def add_cli_arguments(cls, parser) -> None:
        from evoab.config import _positive

        options = parser.add_argument_group("Codex model and SDK")
        options.add_argument(
            "--agent-python",
            help="Python executable whose directory is prepended to Codex shell PATH; disables login shells and shell snapshots",
        )
        options.add_argument(
            "--mcp-timeout-s",
            type=_positive,
            help="Codex MCP acknowledgement timeout in seconds (default: 10800)",
        )
    @classmethod
    def validate_config(cls, args, explicit: set[str]) -> None:
        args.agent_python = validate_agent_python(args.agent_python)
        if not args.model_parallel_tool_calls and not args.provider_url:
            raise ValueError("execution.parallel_tool_calls: false needs the provider gateway "
                             "(--provider-url or EVOAB_PROVIDER_URL) for Codex")
        if (
            any(flag.startswith("--provider-") for flag in explicit)
            and not args.provider_url
        ):
            raise ValueError("provider request options require --provider-url")

    @classmethod
    def config_options(cls, args) -> dict[str, Any]:
        return {
            "reasoning_effort": args.reasoning_effort,
            "api_key": args._api_key,
            "mcp_timeout_s": args.mcp_timeout_s,
            "agent_python": args.agent_python,
        }
