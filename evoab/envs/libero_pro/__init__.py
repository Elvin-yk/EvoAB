"""LIBERO-PRO through the shared motion and policy tools."""

from evoab.envs.libero import LiberoOptions, add_cli_args


def get_integration_spec():
    from evoab.envs.simulation import build_simulation_spec
    from .action_binding import create_binding

    spec = build_simulation_spec("libero_pro", binding_factory=create_binding)
    spec.add_cli_args = add_cli_args
    spec.config_model = LiberoOptions
    spec.deployment_module = "evoab.envs.libero_pro.deployment"
    return spec
