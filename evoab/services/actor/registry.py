"""Actor configuration and lifecycle entry points without native imports."""
from dataclasses import dataclass
from importlib import import_module
from importlib.metadata import entry_points


@dataclass(frozen=True)
class BackendDefinition:
    name: str
    package: str
    backend_class: str

    def resolve_config(self, settings):
        return import_module(f"{self.package}.config").resolve_config(settings)

    def service_limits(self, config):
        return import_module(f"{self.package}.config").service_limits(config)

    def create(self, config):
        factory = getattr(import_module(f"{self.package}.backend"), self.backend_class)
        return factory(config)


_PACKAGE = "evoab.services.actor.backends"
_BACKENDS = {
    "openpi": BackendDefinition("openpi", f"{_PACKAGE}.openpi", "OpenpiBackend"),
    "xpolicylab": BackendDefinition("xpolicylab", f"{_PACKAGE}.xpolicylab", "XPolicyLabBackend"),
    "starvla_calvin": BackendDefinition("starvla_calvin", f"{_PACKAGE}.starvla_calvin", "StarVLACalvinBackend"),
}
_ALIASES = {"pi05": "openpi"}


def backend_names():
    plugins = {p.name for p in entry_points(group="evoab.actor_backends")}
    return (*_BACKENDS, *_ALIASES, *sorted(plugins - _BACKENDS.keys() - _ALIASES.keys()))


def get_backend(name):
    canonical = _ALIASES.get(name, name)
    try:
        return _BACKENDS[canonical]
    except KeyError:
        matches = list(entry_points(group="evoab.actor_backends", name=name))
        if len(matches) != 1:
            raise ValueError(f"Expected one installed actor backend named {name!r}, found {len(matches)}") from None
        backend = matches[0].load()
        if not isinstance(backend, BackendDefinition) or backend.name != name:
            raise ValueError(f"{name}: entry point must export a matching BackendDefinition")
        return backend
