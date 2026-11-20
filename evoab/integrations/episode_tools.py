"""Embodiment-independent tool declarations for prepared episodes."""
from evoab.integrations.composition import ToolModule
from evoab.tools import episode, skills
from evoab.tools.episode import EpisodeTools
from evoab.tools.scene import provider as scene
from evoab.tools.scene.provider import SceneTools
from evoab.tools.skills import SkillTools


def configure_scene(session):
    perception = None
    if session.args.sam3_url:
        from evoab.services.perception.client import PerceptionClient
        perception = session.own(PerceptionClient(session.args.sam3_url, tracer=session.context.tracer))
    return SceneTools(session.episode, perception=perception)


SKILLS_MODULE = ToolModule("skills",
    lambda session: SkillTools(session.episode, writing=session.args.skill_writing),
    skills.tool_policies())
SCENE_MODULE = ToolModule("scene", configure_scene, scene.tool_policies())
EPISODE_MODULE = ToolModule("episode", lambda session: EpisodeTools(session.episode), episode.tool_policies())

