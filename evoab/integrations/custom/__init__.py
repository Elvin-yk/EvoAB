"""User-defined benchmarks: a Gym-style environment described by a manifest.

See ``docs/custom-benchmarks.md`` for the interface and the manifest format.
"""


def get_integration_spec():
    from evoab.integrations.gripper import CLEAN_TOOLS, DIRECT_TOOLS
    from evoab.integrations.simulation import build_simulation_spec
    spec = build_simulation_spec("custom", tool_profiles={"clean": CLEAN_TOOLS, "direct": DIRECT_TOOLS})
    spec.deployment_module = "evoab.integrations.custom.deployment"
    return spec
