"""RoboTwin 2.0 with the shared Direct dual-arm gripper tools."""


def get_integration_spec():
    from evoab.envs.simulation import build_simulation_spec
    spec = build_simulation_spec("robotwin")
    spec.deployment_module = "evoab.envs.robotwin.deployment"
    return spec
