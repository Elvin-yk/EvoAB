"""CALVIN single-task episodes with shared gripper and actor tools."""


def get_integration_spec():
    from evoab.integrations.simulation import build_simulation_spec
    from .action_binding import create_binding

    spec = build_simulation_spec("calvin", binding_factory=create_binding)
    spec.deployment_module = "evoab.integrations.calvin.deployment"
    return spec
