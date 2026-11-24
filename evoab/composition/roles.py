"""Settings for running a non-entry role inside the entry role's session."""

from __future__ import annotations

import copy
from typing import Any

from evoab.composition.compiler import CompiledRole


def role_namespace(args: Any, role: CompiledRole) -> Any:
    """A copy of the run namespace with one role's brain, channel and context applied.

    A role without a pinned agent follows the run's agent. A role without a pinned
    model reuses the run's model: the same value on the same agent, otherwise the
    run's model preset connected for that agent's protocol.
    """
    from evoab.agents.registry import get_agent_backend

    brain = role.role.brain
    agent = role.agent_for(args.agent_backend)
    namespace = copy.copy(args)
    namespace.agent_backend = agent
    backend = get_agent_backend(agent)
    preset = brain.model_preset
    if not preset and not brain.model and agent != args.agent_backend:
        preset = getattr(args, "model_preset_name", None)
        if not preset:
            raise ValueError(f"role {role.name!r} runs {agent} while the entry role runs {args.agent_backend} "
                             f"without a model preset; pin roles.{role.name}.brain.model")
    if preset:
        namespace.model_preset_name = preset
        if getattr(backend, "owns_provider", False):
            namespace.model = preset
            namespace.provider_url = namespace.provider_profile = namespace._api_key = None
        else:
            from evoab.provider.http_presets import preset_for_protocol
            connection = preset_for_protocol(preset, backend.model_protocol)
            namespace.model, namespace.provider_url = connection.model, connection.base_url
            namespace.provider_profile, namespace._api_key = connection.profile, connection.api_key
    else:
        namespace.model = brain.model or args.model
    if brain.reasoning_effort is not None:
        namespace.reasoning_effort = brain.reasoning_effort
    namespace.channel = role.channel
    namespace.direct_config = role.direct
    namespace.context_policy = role.context["context_policy"]
    namespace.context_options = role.context["context_options"]
    namespace.max_turns = role.role.max_turns
    namespace.skill_writing = role.skill_writing
    namespace.tool_style = role.role.style
    if agent != args.agent_backend and not getattr(backend, "owns_provider", False) and not preset:
        namespace.provider_profile = f"passthrough.{backend.model_protocol}"
    return namespace


def validate_roles(args: Any) -> None:
    """Check every non-entry role offline, as the CLI checks the entry role."""
    from evoab.agents.registry import get_agent_backend
    from evoab.provider.registry import validate_provider_profile

    compiled = args.compiled_recipe
    for name, role in compiled.roles.items():
        if name == compiled.recipe.loop.entry:
            continue
        namespace = role_namespace(args, role)
        backend = get_agent_backend(namespace.agent_backend)
        if hasattr(backend, "evaluator_plan"):
            raise ValueError(f"role {name!r}: {namespace.agent_backend} owns its own episode client "
                             "and can only be an entry role")
        if role.context["context_policy"] != "identity" and not namespace.provider_url:
            raise ValueError(f"role {name!r}: context policy {role.context['context_policy']} "
                             "requires --provider-url or EVOAB_PROVIDER_URL")
        if not getattr(backend, "owns_provider", False):
            validate_provider_profile(namespace.provider_profile, client_protocol=backend.model_protocol)
        backend.validate_config(namespace, set())
