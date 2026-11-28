"""The built-in agent: the only agent EvoAB runs.

``loop`` runs the model over the role's calls, ``act/`` holds the three ways of
acting (tools, code, action), ``context/`` assembles prompts, ``models/`` owns
model presets, ``memory/`` carries experience across episodes and ``team/`` runs
several roles in one episode.
"""

from .contracts import AgentResult

__all__ = ["AgentResult"]
