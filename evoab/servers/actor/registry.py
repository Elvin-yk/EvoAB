"""Actor configuration and lifecycle entry points without native imports."""
from dataclasses import dataclass
from importlib import import_module


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


_PACKAGE = "evoab.servers.actor.backends"
_BACKENDS = {
    "starvla_calvin": BackendDefinition("starvla_calvin", f"{_PACKAGE}.starvla_calvin", "StarVLACalvinBackend"),
    "starvla_libero": BackendDefinition("starvla_libero", f"{_PACKAGE}.starvla_libero", "StarVLALiberoBackend"),
}


def backend_names():
    return tuple(_BACKENDS)


def get_backend(name):
    try:
        return _BACKENDS[name]
    except KeyError:
        raise ValueError(f"Unsupported actor backend: {name}") from None
