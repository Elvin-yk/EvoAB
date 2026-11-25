"""Plugin registry and dynamic loader for visual observation processors."""
from __future__ import annotations

import importlib
from typing import Any

from ..config import PluginConfig
from ..contracts import VisualProcessor

_BUILTIN_PLUGINS: dict[str, str] = {
    "sam3": "evoab.observation.toolpackage.plugins.sam3:Sam3Processor",
    "foundationpose": "evoab.observation.toolpackage.plugins.foundationpose:FoundationPoseProcessor",
}


def list_builtin_plugins() -> tuple[str, ...]:
    return tuple(sorted(_BUILTIN_PLUGINS))


def create_processor(plugin: PluginConfig) -> VisualProcessor:
    """Instantiate a built-in plugin by name or an external plugin via ``module:Class``."""
    spec = _BUILTIN_PLUGINS.get(plugin.type, plugin.type)
    if ":" not in spec:
        available = ", ".join(list_builtin_plugins())
        raise ValueError(
            f"Unknown observation plugin {plugin.type!r}. "
            f"Choose a built-in ({available}) or pass 'package.module:ClassName'."
        )
    module_name, attr_name = spec.rsplit(":", 1)
    module = importlib.import_module(module_name)
    factory: Any = getattr(module, attr_name)
    return factory(**dict(plugin.options))
