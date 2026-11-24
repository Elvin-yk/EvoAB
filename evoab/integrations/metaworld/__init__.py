"""MetaWorld MT50 through the shared Direct gripper tools."""


def get_integration_spec():
    from evoab.integrations.simulation import build_simulation_spec

    spec = build_simulation_spec("metaworld")
    spec.deployment_module = "evoab.integrations.metaworld.deployment"
    return spec
