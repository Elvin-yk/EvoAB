"""Manifest of a user-defined benchmark (TOML).

A benchmark supplies ``make(task_id, seed)`` returning an environment with the
usual Gym shape: ``reset()`` returns observations (or ``(obs, info)``) and
``step(action)`` returns ``(obs, reward, done, info)`` or ``(obs, reward,
terminated, truncated, info)``. The manifest names the user's Docker image that
can import it (EvoAB adds its service package on top and starts one container
per episode), where the function lives, and how to read its observations, write
its actions and detect success.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def benchmarks_dir() -> Path:
    """Where registered manifests live (``EVOAB_CONFIG_DIR/benchmarks``)."""
    root = Path(os.environ.get("EVOAB_CONFIG_DIR") or Path.home() / ".config" / "evoab").expanduser()
    return root / "benchmarks"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Task(_Model):
    id: str = Field(min_length=1, pattern=r"^[\w.\-/]+$")
    instruction: str = Field(min_length=1)
    max_steps: int | None = Field(default=None, gt=0)


class Observation(_Model):
    #: Camera name -> observation key holding an HxWx3 uint8 image.
    cameras: dict[str, str] = Field(min_length=1)
    #: Observation key of the end-effector position (metres, world frame).
    ee_pos: str
    #: Observation key of the end-effector orientation quaternion.
    ee_quat: str
    quat_order: Literal["wxyz", "xyzw"] = "wxyz"
    #: Optional observation key of the gripper state and its open/closed values.
    gripper: str | None = None
    gripper_reduce: Literal["first", "sum", "mean", "abs_sum"] = "abs_sum"
    gripper_open: float = 0.08
    gripper_closed: float = 0.0
    #: Images rendered bottom-up (OpenGL convention) are flipped before use.
    flip_vertical: bool = False
    #: What the reported pose refers to, shown to the model.
    pose_description: str = "End-effector pose as reported by the benchmark."


class Action(_Model):
    #: absolute_eef: [x, y, z, quaternion(4), gripper]; delta_eef: [dx, dy, dz, rx, ry, rz, gripper] in [-1, 1].
    type: Literal["absolute_eef", "delta_eef"]
    quat_order: Literal["wxyz", "xyzw"] = "wxyz"
    #: delta_eef: metres and radians reached by a unit action.
    position_scale: float = Field(default=0.05, gt=0)
    rotation_scale: float = Field(default=0.5, gt=0)
    #: Gripper action values that open and close.
    gripper_open: float = -1.0
    gripper_closed: float = 1.0


class Success(_Model):
    #: Read ``info[info_key]``, or call ``env.<method>()`` when ``method`` is set.
    info_key: str | None = "success"
    method: str | None = None

    @model_validator(mode="after")
    def one_source(self):
        if not (self.info_key or self.method):
            raise ValueError("success needs info_key or method")
        return self


class Manifest(_Model):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    title: str
    description: str = ""
    #: Docker image (local or pullable) with the simulator and a Python >= 3.10 that has pip.
    image: str = Field(min_length=1)
    #: Interpreter inside the image that can import the benchmark.
    python: str = Field(default="python3", min_length=1)
    #: Whether the container needs the GPU (NVIDIA Container Toolkit).
    gpu: bool = False
    #: Host directory added to ``sys.path`` before importing ``make``; mounted into the container.
    path: str | None = None
    #: Further host directories mounted at the same paths (checkouts, assets).
    mounts: list[str] = Field(default_factory=list)
    make: str = Field(pattern=r"^[\w.]+:[\w]+$")
    tasks: list[Task] = Field(default_factory=list)
    #: Alternatively ``module:attribute`` resolving to a list (or a callable returning one) of task dicts.
    tasks_from: str | None = Field(default=None, pattern=r"^[\w.]+:[\w]+$")
    embodiment: str = "robot arm"
    simulator: str = ""
    control_hz: float = Field(gt=0)
    max_steps: int = Field(default=300, gt=0)
    #: Embodiment and frame description for Direct prompts; a generic text is used when empty.
    prompt: str = ""
    #: Extra environment variables for the benchmark process (e.g. MUJOCO_GL, PYTHONPATH).
    environment: dict[str, str] = Field(default_factory=dict)
    observation: Observation
    action: Action
    success: Success = Field(default_factory=Success)

    @model_validator(mode="after")
    def has_tasks(self):
        if not self.tasks and not self.tasks_from:
            raise ValueError("declare [[tasks]] or tasks_from")
        return self

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value: str | None) -> str | None:
        if value is not None and not Path(value).expanduser().is_absolute():
            raise ValueError("path must be an absolute host directory")
        return value

    @field_validator("mounts")
    @classmethod
    def absolute_mounts(cls, values: list[str]) -> list[str]:
        if any(not Path(value).expanduser().is_absolute() for value in values):
            raise ValueError("mounts must be absolute host directories")
        return values


def parse(text: str) -> Manifest:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"invalid TOML: {error}") from error
    return Manifest.model_validate(data)


def path_for(name: str) -> Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", name):
        raise ValueError("invalid benchmark name")
    return benchmarks_dir() / f"{name}.toml"


def load(name: str) -> Manifest:
    path = path_for(name)
    if not path.is_file():
        raise ValueError(f"custom benchmark {name!r} is not registered")
    return parse(path.read_text(encoding="utf-8"))


def registered() -> list[str]:
    return sorted(p.stem for p in benchmarks_dir().glob("*.toml")) if benchmarks_dir().is_dir() else []
