"""Resolve and validate configuration before connecting to any service."""

from __future__ import annotations

import argparse
import os
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
        "output_dir",
        "obs_pipeline",
    ):
        if getattr(args, name, None) is None:
            setattr(args, name, runtime_env(name.upper()))
    args.output_dir = args.output_dir or "outputs"
    # The recipe decides whether locate segments (scene option segmentation); the URL only locates SAM3.
    if not args.segmentation:
        args.sam3_url = None
    elif not args.sam3_url:
        raise ValueError("the recipe enables scene segmentation: start SAM3 (evoab deploy sam3) and pass --sam3-url")
    from urllib.parse import urlsplit

    for name in ("env_url", "action_model_url", "sam3_url"):
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
        # The built-in agent: --model names a model preset (evoab models).
        get_agent_backend(args.agent_backend).validate_config(args, explicit)
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


class AgentConfig(BaseModel):
    """The built-in agent: a model preset and its reasoning effort."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str | None = Field(default=None, min_length=1)
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None


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
        values.update(command="run", deploy=None)
        explicit = {"--" + name.replace("_", "-") for name in self.model_fields_set}
        if agent:
            explicit.update("--" + name.replace("_", "-") for name in agent.model_fields_set)
        resolved = resolve_settings(SimpleNamespace(**values), spec, explicit=explicit,
                                    agent_enabled=agent is not None)
        return resolved
