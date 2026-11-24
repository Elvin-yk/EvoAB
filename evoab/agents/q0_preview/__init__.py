"""Launch the original Q0-Preview harness against a prepared RoboDojo episode."""

import json
import os
import sys

from evoab.agents.contracts import AgentLaunch, EvaluatorPlan
from evoab.provider.contracts import PresetCompatibilityError
from evoab.provider.http_presets import resolve_http_preset, validate_http_provider_url

REVISION = "4a9e3afd73b86a48c5db076f5cf9dbfbef1a180e"
MODEL = "claude-opus-5-5"


def _base_url(value):
    validate_http_provider_url(value)
    if not value.startswith("https://") or value.rstrip("/").endswith("/messages"):
        raise PresetCompatibilityError("Q0-Preview requires an HTTPS Anthropic Messages endpoint.")
    # The upstream client appends /v1/messages itself.
    return value.rstrip("/").removesuffix("/v1")


def _preset(name):
    connection = resolve_http_preset(name, client_protocol="messages", profiles=("passthrough.messages",))
    if connection.model != MODEL:
        raise PresetCompatibilityError(f"The upstream Opus configuration requires model {MODEL}.")
    _base_url(connection.base_url)
    return connection


class Q0PreviewAgent:
    model_protocol = "messages"
    owns_provider = True
    workbench_launch = AgentLaunch(
        label="Q0-Preview", integrations=("robodojo",), cli_flags=("--model-preset", "--no-depth"),
        validate_preset=_preset, model_help="Uses the upstream Opus 5.5 planning and review configuration.",
    )

    @classmethod
    def add_cli_arguments(cls, parser):
        group = parser.add_argument_group("Q0-Preview evaluator")
        group.add_argument("--q0-root", help="Upstream XPolicyLab checkout (or EVOAB_Q0_ROOT)")
        group.add_argument("--q0-python", help="Upstream policy Python 3.11 interpreter (or EVOAB_Q0_PYTHON)")
        group.add_argument("--q0-policy-gpu", type=int, help="Policy GPU index within CUDA_VISIBLE_DEVICES")

    @classmethod
    def resolve_defaults(cls, args):
        args.q0_root = getattr(args, "q0_root", None) or os.environ.get("EVOAB_Q0_ROOT")
        args.q0_python = getattr(args, "q0_python", None) or os.environ.get("EVOAB_Q0_PYTHON")
        if getattr(args, "q0_policy_gpu", None) is None:
            args.q0_policy_gpu = int(os.environ.get("EVOAB_Q0_GPU", "0"))

    @classmethod
    def validate_config(cls, args, explicit):
        role = args.recipe.entry
        if (args.command != "run" or args.integration != "robodojo" or args.channel != "tools"
                or args.tool_modules or len(args.compiled_recipe.roles) != 1 or args.tool_debug
                or role.prompt.append or role.prompt.files or role.job_tools is not None or role.initial_observation
                or args.instruction or args.include_extra_task_requirements or args.context_policy != "identity"
                or args.max_turns or args.action_model_url):
            raise ValueError("Q0-Preview requires one RoboDojo evaluator role with its own tools, task and context.")
        if args.reasoning_effort != "xhigh":
            raise ValueError("The upstream policy fixes planning effort at xhigh and checkpoint review at low.")
        if not args.task or args.q0_policy_gpu < 0:
            raise ValueError("Q0-Preview requires a task and a nonnegative policy GPU index.")
        if args.provider_profile not in (None, "passthrough.messages"):
            raise ValueError("Q0-Preview requires the upstream Anthropic Messages profile.")
        if getattr(args, "model_preset", False):
            if "--provider-url" in explicit:
                raise ValueError("--model-preset supplies the Q0-Preview provider URL.")
            connection = _preset(args.model)
            args._q0_base = _base_url(connection.base_url)
            args._q0_key = connection.api_key
        else:
            if args.model != MODEL:
                raise ValueError(f"The upstream configuration requires --model {MODEL}.")
            if "--provider-url" in explicit or not os.environ.get("Q0_PREVIEW_API_KEY"):
                args._q0_base = _base_url(args.provider_url or "https://api.anthropic.com")
                args._q0_key = args._api_key
            else:
                args._q0_base = _base_url(os.environ.get("Q0_PREVIEW_API_BASE_URL") or "https://api.anthropic.com")
                args._q0_key = os.environ["Q0_PREVIEW_API_KEY"]

    @classmethod
    def evaluator_plan(cls, args):
        payload = dict(
            env_url=args.env_url, run_id=args.run_id, task=args.task, identity=getattr(args, "_episode_identity", None),
            root=args.q0_root, python=args.q0_python, gpu=args.q0_policy_gpu, model=MODEL,
            api_base=args._q0_base, api_key=args._q0_key, output=args._session_dir,
            recipe=args.recipe.model_dump(mode="json"), preset=args.model if args.model_preset else None,
        )
        return EvaluatorPlan((sys.executable, "-u", "-m", "evoab.agents.q0_preview.worker"),
                             json.dumps(payload).encode())
