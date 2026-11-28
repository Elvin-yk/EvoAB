"""Bind LIBERO-PRO observations and native OSC actions to the LIBERO actor profile."""

from evoab.envs.libero.action_binding import LiberoBinding


class LiberoProBinding(LiberoBinding):
    integration = "libero_pro"


def create_binding(name, args, description, actor_info):
    if name != LiberoProBinding.name:
        raise ValueError(f"Select {LiberoProBinding.name} for the LIBERO-PRO actor")
    return LiberoProBinding(description, actor_info)
