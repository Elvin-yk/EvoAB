"""RMBench memory-dependent manipulation with shared Direct gripper tools."""


def get_integration_spec():
    from evoab.integrations.simulation import build_simulation_spec
    return build_simulation_spec("rmbench")
