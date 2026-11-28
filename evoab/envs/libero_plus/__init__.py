"""LIBERO-Plus through the shared Direct gripper tools."""


def get_integration_spec():
    from evoab.envs.simulation import build_simulation_spec
    spec = build_simulation_spec("libero_plus")
    spec.deployment_module = "evoab.envs.libero_plus.deployment"
    return spec
