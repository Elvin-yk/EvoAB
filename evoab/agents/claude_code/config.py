"""Claude-only settings and validation of supported model services."""
from evoab.agents.contracts import AgentLaunch
from evoab.provider.http_presets import preset_for_protocol
import os
import argparse
import math
from urllib.parse import urlsplit


CLAUDE_EFFORTS = ("none", "low", "medium", "high", "max")


class ClaudeConfig:
    model_protocol = "messages"
    # Channels this runtime can drive (see evoab/composition).
    # Runs code-channel programs in its own terminal instead of the run_program tool.
    runs_programs = True
    workbench_launch = AgentLaunch(
        label="Claude Code", description="Claude Agent SDK：原生 shell 与进程内工具，支持工具调用与代码通道。", cli_flags=("--model-preset",),
        validate_preset=lambda name: preset_for_protocol(name, "messages"),
        model_help="使用 API Key 的模型预设；需要提供方支持 messages 或可转换的接口。",
    )

    @classmethod
    def add_cli_arguments(cls, parser):
        group = parser.add_argument_group("Claude Code SDK")
        group.add_argument("--claude-max-budget-usd", type=float, default=argparse.SUPPRESS,
                           help="Optional SDK cost ceiling; unset disables the SDK budget cap")

    @classmethod
    def resolve_defaults(cls, args):
        args.claude_max_budget_usd = getattr(args, "claude_max_budget_usd", None)
        if args.reasoning_effort is None:
            args.reasoning_effort = "none"
        if args.provider_url is None:
            from evoab.config import runtime_env
            args.provider_url = runtime_env("PROVIDER_URL") or os.environ.get("ANTHROPIC_BASE_URL")

    @classmethod
    def validate_config(cls, args, explicit):
        if "--mcp-timeout-s" in explicit:
            raise ValueError("--mcp-timeout-s requires --agent codex")
        if args.reasoning_effort not in CLAUDE_EFFORTS:
            raise ValueError(f"Claude Code supports --reasoning-effort {', '.join(CLAUDE_EFFORTS)}")
        if args.context_policy != "identity":
            raise ValueError("Claude Code currently supports only identity context policy")
        if not args.provider_url:
            raise ValueError("Claude Code requires --provider-url or ANTHROPIC_BASE_URL")
        if urlsplit(args.provider_url).scheme not in {"http", "https"}:
            raise ValueError("Claude Messages provider URL must use HTTP or HTTPS")
        if args.claude_max_budget_usd is not None and (
            not math.isfinite(args.claude_max_budget_usd) or args.claude_max_budget_usd <= 0
        ):
            raise ValueError("--claude-max-budget-usd must be positive")

    @classmethod
    def config_options(cls, args):
        # The shared --reasoning-effort; "none" leaves the SDK's effort unset.
        effort = None if args.reasoning_effort == "none" else args.reasoning_effort
        return {"api_key": args._api_key, "max_budget_usd": args.claude_max_budget_usd, "effort": effort}
