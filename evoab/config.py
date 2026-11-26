"""Resolve and validate configuration before connecting to any service."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evoab.context.prompt import get_prompt_builder


def _nonnegative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return number


def _positive(value: str) -> int:
    number = _nonnegative(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def runtime_env(name, default=None):
    """Read a setting from the product's environment-variable namespace."""
    return os.environ.get("EVOAB_" + name, default)


def resolve_config(args, parser, integration_spec, argv=()):
    """CLI adapter: translate shared validation errors into argparse diagnostics."""
    explicit = {word.split("=", 1)[0] for word in argv if word.startswith("--")}
    try:
        resolved = resolve_settings(args, integration_spec, explicit=explicit)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    for owner, options in getattr(parser, "agent_options", {}).items():
        if owner != resolved.agent_backend and (used := explicit & options):
            parser.error(f"{sorted(used)[0]} requires --agent {owner}")
    return resolved


def resolve_settings(args, integration_spec, *, explicit=(), agent_enabled=True):
    """Resolve one runtime configuration without parsing arguments or exiting."""
    explicit = set(explicit)
    from evoab.agents.registry import get_agent_backend
    # One debug switch: verbose logs and raw model requests; the agent's inputs and tools are unchanged.
    args.debug = bool(getattr(args, "debug", False))
    args.log_level = "DEBUG" if args.debug else "INFO"
    if args.debug:
        args.telemetry = "debug"
    resolve_recipe(args, integration_spec, explicit=explicit)
    if not agent_enabled:
        args.agent_backend, args.model = None, None
    if agent_enabled:
        if not getattr(args, "model", None):
            raise ValueError(f"--model is required (or pin roles.{args.recipe.loop.entry}.brain.model "
                             f"in method {args.recipe_id!r})")
        get_agent_backend(args.agent_backend).resolve_defaults(args)
    # Read environment variables at the entry point; business modules receive resolved values.
    for name in (
        "env_url",
        "action_model_url",
        "sam3_url",
        "provider_url",
        "output_dir",
        "obs_pipeline",
    ):
        if getattr(args, name, None) is None:
            setattr(args, name, runtime_env(name.upper()))
    args._api_key = getattr(args, "_api_key", None) or runtime_env("API_KEY")
    if hasattr(args, "_vla_review_api_key") and args._vla_review_api_key is None:
        args._vla_review_api_key = runtime_env("VLA_REVIEW_API_KEY", args._api_key)
    args.output_dir = args.output_dir or "outputs"
    # The recipe decides whether locate segments (scene option segmentation); the URL only locates SAM3.
    if not args.segmentation:
        args.sam3_url = None
    elif not args.sam3_url:
        raise ValueError("the recipe enables scene segmentation: start SAM3 (evoab deploy sam3) and pass --sam3-url")
    from urllib.parse import urlsplit

    for name in ("env_url", "action_model_url", "sam3_url", "provider_url"):
        value = getattr(args, name)
        if value:
            try:
                url = urlsplit(value)
                valid = (
                    url.scheme in {"http", "https", "socket", "tcp", "ws", "wss"}
                    and url.hostname
                    and (url.port is None or url.port > 0)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(
                    f"--{name.replace('_', '-')} must be a valid absolute service URL"
                )
    if args.env_url and urlsplit(args.env_url).scheme not in {"http", "https"}:
        raise ValueError("--env-url must be a prepared episode HTTP(S) URL")
    local = getattr(args, "deploy", None) == "local"
    if local and args.env_url:
        raise ValueError("--deploy local and --env-url (including environment variables) are mutually exclusive")
    if not args.env_url and not local:
        raise ValueError("--env-url or EVOAB_ENV_URL is required (or select --deploy local)")
    if local:
        from evoab.services.deployment.config import validate_deployment
        validate_deployment(args)
    if getattr(args, "obs_pipeline", None):
        from evoab.observation.toolpackage.config import load_pipeline_config

        obs_path = Path(args.obs_pipeline).expanduser().resolve()
        load_pipeline_config(obs_path)
        args.obs_pipeline = str(obs_path)
    if args.observation_profile != "none":
        if args.resource_scheduling != "per-resource":
            raise ValueError(
                "the observation capability requires execution.resource_scheduling per-resource"
            )
        if args.observation_profile != args.integration:
            raise ValueError(
                "the observation profile must match --integration or be none"
            )
        from evoab.observation import load_observation_profile

        load_observation_profile(args.observation_profile)
    if agent_enabled:
        agent_backend = get_agent_backend(args.agent_backend)
        # Roles on another agent reuse the run's model through this preset (composition/roles.py).
        owns = getattr(agent_backend, "owns_provider", False)
        args.model_preset_name = args.model if owns or getattr(args, "model_preset", False) else None
        if getattr(args, "model_preset", False) and not owns:
            if "--provider-url" in explicit or "--provider-profile" in explicit:
                raise ValueError("--model-preset supplies its own provider URL and profile")
            from evoab.provider.http_presets import preset_for_protocol
            connection = preset_for_protocol(args.model, agent_backend.model_protocol)
            args.model, args.provider_url = connection.model, connection.base_url
            args.provider_profile, args._api_key = connection.profile, connection.api_key
        if getattr(agent_backend, "owns_provider", False):
            agent_backend.validate_config(args, explicit)
        else:
            from evoab.provider.registry import validate_provider_profile
            args.provider_profile = args.provider_profile or f"passthrough.{agent_backend.model_protocol}"
            validate_provider_profile(args.provider_profile, client_protocol=agent_backend.model_protocol)
            agent_backend.validate_config(args, explicit)
        if args.context_policy != "identity" and not args.provider_url:
            raise ValueError(
                f"context policy {args.context_policy} requires --provider-url or EVOAB_PROVIDER_URL"
            )
        context = resolve_context_config(args)
        if args.context_policy != "identity":
            from evoab.context.history import create_context_policy

            create_context_policy(args.context_policy, **context["context_options"])
        from evoab.composition.roles import validate_roles
        validate_roles(args)
    get_prompt_builder(args.integration)
    integration_spec.validate_cli(args)
    if getattr(args, "skill_writing", False):
        from evoab.tools.skills import skill_root

        skill_root(writing=True, configured=args.skills_root)
    return args


def resolve_recipe(args, integration_spec, *, explicit=()):
    """Load, override and compile the run's method onto ``args`` (no services opened)."""
    from evoab.composition.compiler import apply_to_args, compile_recipe
    from evoab.composition.methods import get_method
    from evoab.composition.recipe import parse_assignment, with_overrides

    reference = getattr(args, "recipe", None) or integration_spec.default_recipe
    if not isinstance(reference, str):
        raise ValueError("recipe must name a method id or a recipe file")
    method = get_method(reference)
    overrides = dict(getattr(args, "recipe_overrides", None) or {})
    for text in getattr(args, "set", None) or ():
        path, value = parse_assignment(text)
        overrides[path] = value
    recipe = with_overrides(method.recipe, overrides)
    compiled = compile_recipe(recipe, integration_spec, base_dir=method.root)
    apply_to_args(args, compiled, explicit=set(explicit))
    args.recipe_source = method.source
    args.recipe_overrides = overrides
    episodes = recipe.loop.episodes
    experience = getattr(args, "experience_dir", None)
    if episodes.carry and not experience:
        output = args.output_dir or runtime_env("OUTPUT_DIR") or "outputs"
        experience = str(Path(output) / "experience" / recipe.id / args.integration)
    args.experience_dir = str(Path(experience).expanduser().resolve()) if experience else None
    args.skills_root = (str(Path(args.experience_dir) / "skills")
                        if args.experience_dir and "skills" in episodes.carry else None)

def public_config(args) -> dict:
    from urllib.parse import urlsplit, urlunsplit

    # Compiled objects are summarized; the full recipe is saved as recipe.json in the run directory.
    hidden = {"print_config", "validate_config", "recipe", "compiled_recipe", "selected_modules", "direct_config"}
    result = {
        k: str(v) if isinstance(v, Path) else v
        for k, v in vars(args).items()
        if k not in hidden and not k.startswith("_")
    }
    if getattr(args, "recipe_id", None):
        result["recipe"] = args.recipe_id
        result["roles"] = sorted(args.compiled_recipe.roles)
    if getattr(args, "direct_config", None) is not None:
        from dataclasses import asdict
        result["direct_config"] = asdict(args.direct_config)
    for name in ("tool_modules", "selected_tools", "delegates"):
        if name in result:
            result[name] = list(result[name])
    # URLs may contain credentials or query tokens; strip them from all configuration output.
    for name, value in result.items():
        if (name.endswith("_url") or name.endswith("_endpoint")) and value:
            parsed = urlsplit(value)
            result[name] = urlunsplit(
                (parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, "", "")
            )
    return result


def resolve_context_config(args) -> dict:
    """The entry role's compiled context policy (see composition.compiler)."""
    return {"context_policy": getattr(args, "context_policy", "identity"),
            "context_options": dict(getattr(args, "context_options", None) or {})}


@dataclass(frozen=True)
class AgentModelConfig:
    """Resolved main-agent transport settings, separate from SDK options."""

    base_url: str | None
    api_key: str | None
    adapter: str
    request_timeout_s: float
    max_retries: int
    retry_backoff_initial_s: float
    parallel_tool_calls: bool


def resolve_agent_model_config(args) -> AgentModelConfig:
    return AgentModelConfig(
        base_url=args.provider_url,
        api_key=args._api_key,
        adapter=args.provider_profile,
        request_timeout_s=args.provider_timeout_s,
        max_retries=args.provider_retries,
        retry_backoff_initial_s=args.provider_retry_backoff_s,
        parallel_tool_calls=args.model_parallel_tool_calls,
    )




class AgentConfig(BaseModel):
    """Built-in planner settings; credentials are excluded from serialized configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str | None = Field(default=None, min_length=1)
    # None uses the recipe's entry-role brain.
    agent_backend: str | None = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    provider_url: str | None = None
    provider_profile: str | None = None
    api_key: str | None = Field(default=None, exclude=True, repr=False)
    provider_timeout_s: int = Field(default=600, gt=0)
    provider_retries: int = Field(default=2, ge=0)
    provider_retry_backoff_s: int = Field(default=5, ge=0)
    agent_python: str | None = None
    opencode_binary: str = "opencode"
    opencode_version: str | None = None
    gemini_binary: str = "gemini"
    gemini_version: str | None = None
    gemini_bridge_manifest: str | None = None
    mcp_timeout_s: int | None = Field(default=None, gt=0)
    claude_max_budget_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class SessionConfig(BaseModel):
    """One prepared episode. Integration-specific options belong to its registered schema."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    integration: str = Field(min_length=1)
    env_url: str | None = None
    agent: AgentConfig | None = None
    integration_options: dict[str, Any] = Field(default_factory=dict, repr=False)
    instruction: str | None = None
    action_model_url: str | None = None
    sam3_url: str | None = None
    obs_pipeline: Path | None = None
    output_dir: Path | None = None
    episode_timeout_s: int = Field(default=10800, ge=0)
    # Method id or recipe file; None uses the integration's default method.
    recipe: str | None = None
    # Dotted recipe paths or declared parameter names, e.g. {"horizon": "3:10"}.
    recipe_overrides: dict[str, Any] = Field(default_factory=dict)
    experience_dir: Path | None = None
    save_exchanges: bool = True
    telemetry: Literal["off", "standard"] = "standard"
    debug: bool = False

    def resolve(self):
        """Validate offline and return the internal shared runtime settings."""
        from evoab.integrations import load_integration

        spec = load_integration(self.integration).get_integration_spec()
        options = spec.config_model.model_validate(self.integration_options)
        values = self.model_dump(exclude={"agent", "integration_options"})
        values.update({name: getattr(options, name) for name in type(options).model_fields})
        # Tool-only sessions retain model transport defaults for shared tool composition,
        # but do not load, validate or instantiate an agent backend.
        agent = self.agent
        values.update(agent.model_dump(exclude={"api_key"}) if agent else {
            name: field.get_default() for name, field in AgentConfig.model_fields.items()
            if name not in {"model", "api_key"}
        })
        values.update(command="run", deploy=None, _api_key=agent.api_key if agent else None)
        if "vla_review_api_key" in values:
            values["_vla_review_api_key"] = values.pop("vla_review_api_key")
        explicit = {"--" + name.replace("_", "-") for name in self.model_fields_set}
        if agent:
            explicit.update("--" + name.replace("_", "-") for name in agent.model_fields_set)
            if "agent_backend" in agent.model_fields_set and agent.agent_backend is not None:
                explicit.add("--agent")
        resolved = resolve_settings(SimpleNamespace(**values), spec, explicit=explicit,
                                    agent_enabled=agent is not None)
        if agent:
            from evoab.agents.registry import get_agent_backend

            backend = resolved.agent_backend
            if hasattr(get_agent_backend(backend), "evaluator_plan"):
                raise ValueError(f"{backend} requires the evoab run CLI")
            for prefix, owner in (("gemini_", "gemini_cli"), ("opencode_", "opencode"),
                                  ("claude_", "claude_code")):
                if backend != owner and any(name.startswith(prefix) for name in agent.model_fields_set):
                    raise ValueError(f"{prefix.rstrip('_').title()} settings require agent_backend={owner!r}")
            if backend != "codex" and any(name == "agent_python" or name.startswith("mcp_")
                                          for name in agent.model_fields_set):
                raise ValueError("Codex settings require agent_backend='codex'")
        return resolved
