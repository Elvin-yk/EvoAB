"""MetaWorld MT50 through the shared motion and policy tools."""


def get_integration_spec():
    from evoab.integrations.simulation import build_simulation_spec
    from .action_binding import create_binding

    spec = build_simulation_spec("metaworld", binding_factory=create_binding)
    spec.deployment_module = "evoab.integrations.metaworld.deployment"
    return spec
