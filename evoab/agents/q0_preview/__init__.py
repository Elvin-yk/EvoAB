"""Launch the original Q0-Preview harness against a prepared RoboDojo episode."""

import json
import os
from pathlib import Path
import sys

from evoab.agents.contracts import AgentLaunch, EvaluatorPlan
from evoab.provider.contracts import PresetCompatibilityError
from evoab.provider.http_presets import resolve_http_preset, validate_http_provider_url

REVISION = "4a9e3afd73b86a48c5db076f5cf9dbfbef1a180e"
MODEL = "claude-opus-5-5"


def _agent_config(path):
    if not path:
        return {"provider": "anthropic", "model": MODEL}
    try:
        config = json.loads(Path(path).expanduser().read_text())
    except (OSError, ValueError) as error:
        raise PresetCompatibilityError("The selected Q0 agent configuration must be a readable JSON file.") from error
    if (not isinstance(config, dict) or config.get("provider") not in ("anthropic", "openai")
            or not isinstance(config.get("model"), str) or not config["model"].strip()):
        raise PresetCompatibilityError("Select a Q0 agent config with an upstream provider and model.")
    if any(field in config for field in ("api_key", "token", "password", "headers")):
        raise PresetCompatibilityError("Q0 credentials must be supplied through the environment.")
    if config["provider"] == "openai" and config.get("stream", False) is not False:
        raise PresetCompatibilityError("The upstream OpenAI Responses transport requires stream=false.")
    key_env = config.get("api_key_env", "Q0_PREVIEW_API_KEY")
    if not isinstance(key_env, str) or not key_env.isidentifier():
        raise PresetCompatibilityError("Q0 api_key_env must name an environment variable.")
    if config.get("base_url"):
        _base_url(config["base_url"], _protocol(config))
    return config


def _protocol(config):
    return "messages" if config["provider"] == "anthropic" else "responses"


def _base_url(value, protocol):
    if not isinstance(value, str):
        raise PresetCompatibilityError("Q0-Preview requires an HTTPS API base URL.")
    validate_http_provider_url(value)
    base = value.rstrip("/")
    if not base.startswith("https://") or base.endswith(("/messages", "/responses")):
        raise PresetCompatibilityError("Q0-Preview requires an HTTPS API base URL.")
    # Upstream appends /v1/messages for Anthropic and /responses for OpenAI.
    return base.removesuffix("/v1") if protocol == "messages" else base


def _preset(name, config=None):
    if config is None:
        config = _agent_config(os.environ.get("Q0_PREVIEW_AGENT_CONFIG"))
    protocol = _protocol(config)
    connection = resolve_http_preset(name, client_protocol=protocol, profiles=(f"passthrough.{protocol}",))
    if connection.model != config["model"]:
        raise PresetCompatibilityError(f"The selected upstream configuration requires model {config['model']}.")
    _base_url(connection.base_url, protocol)
    return connection


class Q0PreviewAgent:
    model_protocol = "messages"
    owns_provider = True
    workbench_launch = AgentLaunch(
        label="Q0-Preview", integrations=("robodojo",), cli_flags=("--model-preset",),
        validate_preset=_preset, model_help="Select a preset matching the upstream Q0 agent configuration.",
    )

    @classmethod
    def add_cli_arguments(cls, parser):
        group = parser.add_argument_group("Q0-Preview evaluator")
        group.add_argument("--q0-root", help="Upstream XPolicyLab checkout (or EVOAB_Q0_ROOT)")
        group.add_argument("--q0-python", help="Upstream policy Python 3.11 interpreter (or EVOAB_Q0_PYTHON)")
        group.add_argument("--q0-agent-config", help="Upstream agent JSON (or Q0_PREVIEW_AGENT_CONFIG)")
        group.add_argument("--q0-policy-gpu", type=int, help="Policy GPU index within CUDA_VISIBLE_DEVICES")

    @classmethod
    def resolve_defaults(cls, args):
        args.q0_root = getattr(args, "q0_root", None) or os.environ.get("EVOAB_Q0_ROOT")
        args.q0_python = getattr(args, "q0_python", None) or os.environ.get("EVOAB_Q0_PYTHON")
        args.q0_agent_config = getattr(args, "q0_agent_config", None) or os.environ.get("Q0_PREVIEW_AGENT_CONFIG")
        if getattr(args, "q0_policy_gpu", None) is None:
            args.q0_policy_gpu = int(os.environ.get("EVOAB_Q0_GPU", "0"))

    @classmethod
    def validate_config(cls, args, explicit):
        role = args.recipe.entry
        if (args.command != "run" or args.integration != "robodojo" or args.channel != "tools"
                or args.tool_modules or len(args.compiled_recipe.roles) != 1 or args.tool_debug
                or role.prompt.append or role.prompt.files or role.job_tools is not None or role.initial_observation
                or args.instruction or args.task_supplements or args.context_policy != "identity"
                or args.max_turns or args.action_model_url):
            raise ValueError("Q0-Preview requires one RoboDojo evaluator role with its own tools, task and context.")
        if args.reasoning_effort != "xhigh":
            raise ValueError("The upstream policy fixes planning effort at xhigh and checkpoint review at low.")
        if not args.task or args.q0_policy_gpu < 0:
            raise ValueError("Q0-Preview requires a task and a nonnegative policy GPU index.")
        config = _agent_config(args.q0_agent_config)
        protocol = _protocol(config)
        if args.provider_profile not in (None, f"passthrough.{protocol}"):
            raise ValueError(f"The selected Q0 configuration requires the {protocol} protocol.")
        args._q0_model = config["model"]
        args._q0_config = config if args.q0_agent_config else None
        if getattr(args, "model_preset", False):
            if "--provider-url" in explicit:
                raise ValueError("--model-preset supplies the Q0-Preview provider URL.")
            connection = _preset(args.model, config)
            args._q0_base = _base_url(connection.base_url, protocol)
            args._q0_key = connection.api_key
        else:
            if args.model != config["model"]:
                raise ValueError(f"The upstream configuration requires --model {config['model']}.")
            default = "https://api.anthropic.com" if protocol == "messages" else "https://api.openai.com/v1"
            key = os.environ.get(config.get("api_key_env", "Q0_PREVIEW_API_KEY"))
            if "--provider-url" in explicit or (args._api_key and not key):
                args._q0_base = _base_url(args.provider_url or default, protocol)
                args._q0_key = args._api_key or key
            else:
                base = os.environ.get("Q0_PREVIEW_API_BASE_URL") or config.get("base_url") or default
                args._q0_base = _base_url(base, protocol)
                args._q0_key = key

    @classmethod
    def evaluator_plan(cls, args):
        payload = dict(
            env_url=args.env_url, run_id=args.run_id, task=args.task, identity=getattr(args, "_episode_identity", None),
            root=args.q0_root, python=args.q0_python, gpu=args.q0_policy_gpu, model=args._q0_model,
            agent_config=args._q0_config,
            api_base=args._q0_base, api_key=args._q0_key, output=args._session_dir,
            recipe=args.recipe.model_dump(mode="json"), preset=args.model if args.model_preset else None,
        )
        return EvaluatorPlan((sys.executable, "-u", "-m", "evoab.agents.q0_preview.worker"),
                             json.dumps(payload).encode())
