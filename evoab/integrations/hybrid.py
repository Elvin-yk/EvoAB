"""Shared actor configuration and lifecycle assembly for any tool family."""
from __future__ import annotations

import argparse
import re
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from evoab.integrations.base import IntegrationOptions
from evoab.integrations.composition import ToolModule
from evoab.provider.registry import list_provider_profiles, validate_provider_profile
from evoab.services.actor.client import ActorClient
from evoab.tools.hybrid.provider import NAMES as HYBRID_NAMES, HybridTools, tool_policies
from evoab.tools.hybrid.reviewer import ChunkReviewer


class PolicyToolOptions(BaseModel):
    """How the agent's policy tool behaves: the environment binding and the chunk reviewer.

    ``None`` keeps the environment's default binding and the binding's reviewer default.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    binding: str | None = None
    review: bool | None = None


class HybridOptions(IntegrationOptions):
    action_model_checkpoint_sha256: str | None = None
    vla_review_model: str | None = None
    vla_review_base_url: str | None = None
    vla_review_adapter: str | None = None
    vla_review_api_key: str | None = Field(default=None, exclude=True, repr=False)


def add_hybrid_cli_args(parser: argparse.ArgumentParser) -> None:
    """Configure actor execution independently of the environment embodiment."""
    group = parser.add_argument_group("Policy service (the hybrid capability's binding and reviewer are recipe options)")
    group.add_argument("--action-model-checkpoint-sha256",
        dest="action_model_checkpoint_sha256",
    )
    group.add_argument("--vla-review-model")
    group.add_argument("--vla-review-base-url")
    group.add_argument("--vla-review-adapter", choices=list_provider_profiles(client_protocol="responses"))
    group.add_argument("--vla-review-api-key", dest="_vla_review_api_key")


def validate_hybrid_cli(args: Any) -> None:
    """Validate actor and reviewer settings without opening a service."""
    args.vla_review_model = args.vla_review_model or args.model
    # A separate reviewer endpoint/profile is resolved when the action binding
    # decides whether review is enabled. Messages endpoints cannot be inherited
    # merely because their bodies are passed through unchanged.
    if args.provider_profile in list_provider_profiles(client_protocol="responses"):
        args.vla_review_base_url = args.vla_review_base_url or args.provider_url
        args.vla_review_adapter = args.vla_review_adapter or args.provider_profile
    if args.vla_review_adapter:
        validate_provider_profile(args.vla_review_adapter, client_protocol="responses")
    if "hybrid" in args.tool_modules and args.vla_review is True:
        _validate_reviewer_endpoint(args)
    if args.vla_review_base_url:
        address = urlsplit(args.vla_review_base_url)
        if address.scheme not in {"http", "https"} or not address.hostname:
            raise ValueError("Reviewer endpoint must be an absolute HTTP(S) URL")
    binding = getattr(args, "action_model_binding", None)
    checkpoint = getattr(args, "action_model_checkpoint_sha256", None)
    if binding and not args.action_model_url and _is_direct_binding(args.integration, binding):
        if checkpoint:
            raise ValueError("An external actor checkpoint requires --action-model-url")
        return
    if "hybrid" in args.tool_modules or binding:
        if not args.action_model_url:
            raise ValueError("Hybrid requires --action-model-url")
        if not checkpoint or re.fullmatch(r"[0-9a-f]{64}", checkpoint) is None:
            raise ValueError("Hybrid requires --action-model-checkpoint-sha256 with 64 lowercase hex characters")
    if args.action_model_url:
        address = urlsplit(args.action_model_url)
        if (address.scheme not in {"http", "https"} or not address.hostname
                or address.query or address.fragment):
            raise ValueError("Actor endpoint must be an absolute HTTP(S) base URL")


def _is_direct_binding(integration, binding):
    from evoab.integrations import load_integration
    return binding is not None and binding == load_integration(integration).get_integration_spec().direct_policy_binding


def _direct_actors(session):
    from evoab.agents.registry import get_agent_backend
    from evoab.services.actor.backends.direct.config import DirectActorConfig
    from evoab.services.actor.backends.direct.local import LocalDirectActor

    args = session.args
    environment = session.episode.description.environment
    actors = {}
    for name, role in args.compiled_recipe.roles.items():
        if "hybrid" not in role.tool_modules:
            continue
        brain = role.role.brain
        preset = getattr(args, "model_preset_name", None)
        if name != args.recipe.loop.entry:
            if brain.model:
                backend = get_agent_backend(role.agent_for(args.agent_backend))
                preset = brain.model if getattr(backend, "owns_provider", False) else None
            else:
                preset = brain.model_preset or preset
        if not preset:
            raise ValueError(f"Direct needs the calling role {name!r} to use a model preset")
        config = DirectActorConfig(model_preset=preset,
            arms=[arm.name for arm in environment.arms], cameras=[camera.name for camera in environment.cameras],
            control_frequency_hz=environment.control_frequency_hz)
        actors[name] = session.own(LocalDirectActor(config))
    return actors


def _validate_reviewer_endpoint(args):
    if not args.vla_review_model:
        raise ValueError("An enabled reviewer requires --vla-review-model or an agent model")
    if not args.vla_review_adapter or not args.vla_review_base_url:
        raise ValueError("An enabled reviewer requires a Responses-compatible "
                         "--vla-review-adapter and --vla-review-base-url; "
                         "a Messages planner endpoint cannot be inherited")
    validate_provider_profile(args.vla_review_adapter, client_protocol="responses")


def configure_hybrid(session, selection=None):
    """Own services here; bindings only translate between declared contracts."""
    args, episode = session.args, session.episode
    binding_factory = session.binding_factory
    if binding_factory is None:
        raise ValueError("This integration has no hybrid binding factory")
    options = selection.options if selection is not None else PolicyToolOptions()
    name = options.binding or getattr(episode.description.environment, "action_binding", None)
    role_actors = {}
    if not args.action_model_url and _is_direct_binding(args.integration, name):
        role_actors = _direct_actors(session)
        actor = next(iter(role_actors.values()))
    else:
        checkpoint = args.action_model_checkpoint_sha256
        if not checkpoint:
            raise ValueError("Hybrid requires --action-model-checkpoint-sha256")
        actor = session.own(ActorClient(args.action_model_url,
            checkpoint_sha256=checkpoint, tracer=session.context.tracer))
    binding = binding_factory(name, args, episode.description, actor.info)
    enabled = binding.reviewer_supported if options.review is None else options.review
    if enabled and not binding.reviewer_supported:
        raise ValueError("The selected binding does not support actor review")
    if enabled:
        _validate_reviewer_endpoint(args)
    reviewer = session.own(ChunkReviewer(enabled=enabled,
        model=args.vla_review_model, base_url=args.vla_review_base_url,
        api_key=args._vla_review_api_key, adapter=args.vla_review_adapter,
        prompt_path=binding.reviewer_prompt, validator=binding.validate_decision,
        tracer=session.context.tracer))
    session.action_model_name = ", ".join(dict.fromkeys(a.info.model for a in role_actors.values())) or actor.info.model
    return HybridTools(episode, actor=actor, binding=binding, reviewer=reviewer,
                       role_actors=role_actors, entry_role=args.recipe.loop.entry if role_actors else None)


HYBRID_MODULE = ToolModule("hybrid", configure_hybrid, tool_policies(), tools=HYBRID_NAMES, options=PolicyToolOptions,
    label="Action model (Hybrid)", description="Ask a VLA / world-action model for bounded, reviewed action chunks.",
    runtime_prompts=("hybrid.md",))
