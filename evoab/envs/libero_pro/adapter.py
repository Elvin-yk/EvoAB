"""Publish the language of the selected LIBERO-PRO BDDL problem."""

from evoab.envs.libero.adapter import LiberoPolicyAdapter


class LiberoProPolicyAdapter(LiberoPolicyAdapter):
    def __init__(self, env, obs, **kwargs):
        # Perturbed BDDL problems can share their filename with another instruction.
        kwargs["instruction"] = env.language_instruction
        super().__init__(env, obs, **kwargs)
