"""User-defined benchmarks: a Gym-style environment described by a manifest.

Existing manifests keep running; Studio and the CLI no longer register new ones.
The environment is ``make(task_id, seed)`` returning a Gym-style env; see
``examples/benchmarks/libero_gym.py`` for a wrapper and ``manifest.py`` for the format.
"""


def get_integration_spec():
    from evoab.envs.simulation import build_simulation_spec
    spec = build_simulation_spec("custom")
    spec.deployment_module = "evoab.envs.custom.deployment"
    return spec
