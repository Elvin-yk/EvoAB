"""Embodiment-independent tool modules for prepared episodes."""
from pydantic import BaseModel, ConfigDict

from evoab.envs.composition import ModuleSelection, ToolModule
from evoab.tools import episode, skills
from evoab.tools.episode import EpisodeTools
from evoab.tools.scene import provider as scene
from evoab.tools.scene.provider import SceneTools
from evoab.tools.skills import SkillTools


class SkillOptions(BaseModel):
    """``write`` adds write_skill for environment-local experience."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    write: bool = False


class SceneOptions(BaseModel):
    """``segmentation`` lets ``locate`` segment named objects with SAM3 (needs ``--sam3-url``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    segmentation: bool = False


def configure_scene(session, selection: ModuleSelection):
    names = selection.tools or scene.NAMES
    perception = None
    if "locate" in names and selection.options.segmentation:
        from evoab.servers.perception.client import PerceptionClient
        perception = session.own(PerceptionClient(session.args.sam3_url, tracer=session.context.tracer))
    return SceneTools(session.episode, perception=perception, names=names)


def configure_skills(session, selection: ModuleSelection):
    return SkillTools(session.episode, writing=selection.options.write, names=selection.tools,
                      root=getattr(session.args, "skills_root", None))


SKILLS_MODULE = ToolModule("skills", configure_skills, skills.tool_policies(), tools=(*skills.NAMES, "write_skill"), options=SkillOptions,
    label="Skill library", description="List and read Markdown skills; optionally write environment-local experience.")
SCENE_MODULE = ToolModule("scene", configure_scene, scene.tool_policies(), tools=scene.NAMES, options=SceneOptions,
    label="Scene perception", description="Measured state, calibrated multi-view capture and 2D segmentation.")
EPISODE_MODULE = ToolModule("episode", lambda session, selection: EpisodeTools(session.episode),
    episode.tool_policies(), tools=episode.NAMES, label="Episode", description="Submit a completion claim; the environment decides success.")


class UserToolsOptions(BaseModel):
    """``file``: a Python file of ``@tool`` functions (see ``evoab.tools.user``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    file: str


def configure_user_tools(session, selection: ModuleSelection):
    from evoab.tools.user import UserTools, load_tools
    return UserTools(session.episode, load_tools(selection.options.file))


USER_TOOLS_MODULE = ToolModule("user_tools", configure_user_tools, options=UserToolsOptions,
    label="Your tools", description="Python functions marked with @tool, from the file in options.file.")
