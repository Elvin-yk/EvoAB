"""RoboTwin 2.0 with the shared Direct dual-arm gripper tools."""


def get_integration_spec():
    from evoab.integrations.gripper import CLEAN_TOOLS, DIRECT_TOOLS
    from evoab.integrations.simulation import build_simulation_spec
    spec = build_simulation_spec("robotwin", tool_profiles={"clean": CLEAN_TOOLS, "direct": DIRECT_TOOLS})
    spec.deployment_module = "evoab.integrations.robotwin.deployment"
    return spec
