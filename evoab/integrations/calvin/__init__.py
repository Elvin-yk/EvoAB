"""CALVIN single-task episodes with the shared Direct gripper tools."""


def get_integration_spec():
    from evoab.integrations.simulation import build_simulation_spec
    spec = build_simulation_spec("calvin")
    spec.deployment_module = "evoab.integrations.calvin.deployment"
    return spec
