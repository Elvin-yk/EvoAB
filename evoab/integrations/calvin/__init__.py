"""CALVIN single-task episodes with the shared Direct gripper tools."""


def get_integration_spec():
    from evoab.integrations.gripper import CLEAN_TOOLS, DIRECT_TOOLS
    from evoab.integrations.simulation import build_simulation_spec
    return build_simulation_spec("calvin", tool_profiles={"clean": CLEAN_TOOLS, "direct": DIRECT_TOOLS})
