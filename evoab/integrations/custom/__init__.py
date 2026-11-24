"""User-defined benchmarks: a Gym-style environment described by a manifest.

See ``docs/custom-benchmarks.md`` for the interface and the manifest format.
"""


def get_integration_spec():
    from evoab.integrations.simulation import build_simulation_spec
    spec = build_simulation_spec("custom")
    spec.deployment_module = "evoab.integrations.custom.deployment"
    return spec
