"""Environment integration registration and session contracts."""

from __future__ import annotations

import importlib
from typing import Any

from evoab.envs.base import (
    EpisodeOutcome,
    IntegrationContext,
    IntegrationSession,
)

_INTEGRATION_REGISTRY: dict[str, Any] = {}


def load_integration(name: str) -> Any:
    """Load an integration lazily and validate its session entry point."""
    if name not in _INTEGRATION_REGISTRY:
        # Integrations expose their session factories through one namespace.
        module_name = f"evoab.envs.{name}"
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as error:
            if error.name != module_name:
                raise
            raise ValueError(f"Integration {name!r} is not available in this installation") from error
        if not callable(getattr(module, "get_integration_spec", None)):
            raise ValueError(f"Integration {name!r} is not available in this installation")
        spec = module.get_integration_spec()
        if not callable(getattr(spec, "create_session", None)):
            raise AttributeError(f"evoab.envs.{name} does not provide create_session")
        _INTEGRATION_REGISTRY[name] = module
    return _INTEGRATION_REGISTRY[name]


def resolve_benchmark(name: str) -> tuple[str, str | None]:
    """Map a benchmark name to its integration package and, for a custom one, its manifest.

    Users name benchmarks: ``libero``, ``libero_pro``, ``calvin`` or a custom
    benchmark registered under ``~/.config/evoab/benchmarks/NAME.toml``. A custom
    benchmark runs through the ``custom`` integration with its name as the manifest.
    """
    if not name:
        raise ValueError("Name a benchmark with --benchmark (see evoab benchmarks list)")
    if name == "custom":
        raise ValueError("Name the custom benchmark itself: --benchmark NAME, registered as "
                         "~/.config/evoab/benchmarks/NAME.toml")
    try:
        load_integration(name)
        return name, None
    except ValueError:
        pass
    from evoab.envs.custom import manifest
    try:
        registered = manifest.path_for(name).is_file()
    except ValueError:
        registered = False
    if registered:
        return "custom", name
    raise ValueError(f"Unknown benchmark {name!r} (see evoab benchmarks list)")


def apply_benchmark(args) -> None:
    """Set the internal selection (integration, manifest or suite, native task) from --benchmark/--task.

    Task IDs are what ``evoab benchmarks tasks`` lists: ``libero_spatial/pick_up_...`` for
    benchmarks with suites, the plain name otherwise. One image serves every suite.
    """
    args.integration, args.runtime_profile = resolve_benchmark(args.benchmark)
    task = getattr(args, "task", None)
    if task and "/" in task:
        suite, _, name = task.rpartition("/")
        if args.runtime_profile is not None:
            raise ValueError(f"Custom benchmark {args.benchmark} task IDs have no suite: {task!r}")
        args.runtime_profile, args.task = suite, name


__all__ = [
    "EpisodeOutcome", "IntegrationContext", "IntegrationSession",
    "apply_benchmark", "load_integration", "resolve_benchmark",
]
