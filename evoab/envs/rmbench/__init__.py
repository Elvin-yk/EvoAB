"""RMBench memory-dependent simulation with shared motion tools."""

def get_integration_spec():
    from evoab.envs.simulation import build_simulation_spec

    def binding_factory(name, args, description, actor_info):
        from .action_binding import create_binding

        return create_binding(name, args, description, actor_info)

    return build_simulation_spec("rmbench", binding_factory=binding_factory)
