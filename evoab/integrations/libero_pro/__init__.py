"""LIBERO-PRO through the shared Direct gripper tools."""


def get_integration_spec():
    from evoab.integrations.simulation import build_simulation_spec
    spec = build_simulation_spec("libero_pro")
    spec.deployment_module = "evoab.integrations.libero_pro.deployment"
    return spec
