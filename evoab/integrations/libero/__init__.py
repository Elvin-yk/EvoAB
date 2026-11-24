"""Original LIBERO with the shared gripper tools."""
from evoab.integrations.gripper import GripperOptions, add_gripper_cli_args


class LiberoOptions(GripperOptions):
    init_state_index: int = 0


def add_cli_args(parser):
    add_gripper_cli_args(parser)
    parser.add_argument("--init-state-index", type=int, default=0,
                        help="Index in the task's official list of initial states (default: 0)")


def get_integration_spec():
    from evoab.integrations.simulation import build_simulation_spec

    spec = build_simulation_spec("libero")
    spec.add_cli_args = add_cli_args
    spec.config_model = LiberoOptions
    spec.deployment_module = "evoab.integrations.libero.deployment"
    return spec
