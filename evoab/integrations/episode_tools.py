"""Embodiment-independent tool modules for prepared episodes."""
from pydantic import BaseModel, ConfigDict

from evoab.integrations.composition import ModuleSelection, ToolModule
from evoab.tools import episode, skills
from evoab.tools.episode import EpisodeTools
from evoab.tools.scene import provider as scene
from evoab.tools.scene.provider import SceneTools
from evoab.tools.skills import SkillTools


class SkillOptions(BaseModel):
    """``write`` adds write_skill for environment-local experience."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    write: bool = False


def configure_scene(session, selection: ModuleSelection):
    names = selection.tools or scene.NAMES
    perception = None
    if "locate" in names and session.args.sam3_url:
        from evoab.services.perception.client import PerceptionClient
        perception = session.own(PerceptionClient(session.args.sam3_url, tracer=session.context.tracer))
    return SceneTools(session.episode, perception=perception, names=names)


def configure_skills(session, selection: ModuleSelection):
    return SkillTools(session.episode, writing=selection.options.write,
                      root=getattr(session.args, "skills_root", None))


SKILLS_MODULE = ToolModule("skills", configure_skills, skills.tool_policies(), options=SkillOptions,
    label="Skill library", description="List and read Markdown skills; optionally write environment-local experience.")
SCENE_MODULE = ToolModule("scene", configure_scene, scene.tool_policies(), tools=scene.NAMES,
    label="Scene perception", description="Measured state, calibrated multi-view capture and 2D segmentation.")
EPISODE_MODULE = ToolModule("episode", lambda session, selection: EpisodeTools(session.episode),
    episode.tool_policies(), tools=episode.NAMES, label="Episode", description="Submit a completion claim; the environment decides success.")
