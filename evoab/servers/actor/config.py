"""Resolve one actor backend and its settings before importing native runtimes."""
from dataclasses import dataclass

from pydantic import TypeAdapter

from .registry import BackendDefinition, get_backend


@dataclass(frozen=True)
class ResolvedActor:
    definition: BackendDefinition
    config: object


def resolve_actor(settings: dict, *, backend: str | None = None) -> ResolvedActor:
    values = dict(settings)
    configured = values.pop("backend", None)
    requested = backend or configured
    if not requested:
        raise ValueError("Choose an actor backend (--backend or backend in the config)")
    definition = get_backend(requested)
    return ResolvedActor(definition, definition.resolve_config(values))


def apply_service_limits(resolved, batching, sessions):
    """Default omitted capacities and reject explicitly incompatible service settings."""
    limits = resolved.definition.service_limits(resolved.config)
    for name, maximum in limits.items():
        if name not in {"max_batch_size", "max_sessions"} or type(maximum) is not int or maximum < 1:
            raise ValueError("Actor service limits must declare positive batch or session capacities")
        settings = batching if name == "max_batch_size" else sessions
        value = TypeAdapter(int).validate_python(settings.setdefault(name, maximum))
        if value > maximum:
            raise ValueError(f"Actor backend {resolved.definition.name} requires {name}<={maximum}")
