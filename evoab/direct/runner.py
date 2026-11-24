"""Direct policy: the model emits action chunks that the environment controller executes.

Each inference is one independent model request (no retained replies): the
prompt pack, the task, and a short window of recent observations. The model
must answer with exactly one action-chunk tool call; the chunk is decoded and
step-limited completely, then executed one row per control step through the
environment's own controller, after which the policy observes and replans.
Malformed output is re-requested on the unchanged observation; if every attempt
fails, the policy records the inference error and ends without advancing the environment.

Records (under ``direct/`` in the run directory):

``policy.json``   configuration, model, tool schema and prompt blocks
``prompt.md``     the exact system prompt
``frames.jsonl``  one line per control step: images and measured state
``calls.jsonl``   one line per inference: timing, attempts, chunk, execution
``requests/``     each request's messages, images referenced by file
"""

from __future__ import annotations

import base64
import io
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


from evoab.direct import prompts
from evoab.direct.actions import REPRESENTATIONS, JointRow, Limits, OutputError, Target
from evoab.services.environment.contracts import (
    ArmCommand, CaptureRequest, JointCommand, JointTarget, PoseCommand, Pose, StepRequest,
)
from evoab.telemetry.logging import get_logger

logger = get_logger("direct")


@dataclass
class DirectConfig:
    representation: str = "abs_eef"
    min_horizon: int = 3
    max_horizon: int = 15
    history_length: int = 15
    history_stride: int = 7
    blocks: tuple[str, ...] = prompts.BLOCKS
    robot_prompt: str | None = None
    translation_limit_m: float = 0.02
    rotation_limit_rad: float = 0.1
    joint_step_limit_rad: float = 0.05
    repair_attempts: int = 2
    image_max_side: int = 384
    jpeg_quality: int = 85
    max_calls: int = 0  # 0: until the episode ends
    custom_prompt: str = ""


@dataclass
class DirectResult:
    calls: int = 0
    steps: int = 0
    stop_reason: str = ""
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    elapsed_s: float = 0.0


def _jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def _data_url(path: str, max_side: int, quality: int) -> str:
    from PIL import Image
    with Image.open(path) as image:
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side))
        stream = io.BytesIO()
        image.save(stream, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()


class DirectPolicy:
    def __init__(self, episode, model, *, integration: str, instruction: str, output_dir: Path,
                 config: DirectConfig | None = None):
        self.episode, self.model, self.integration = episode, model, integration
        self.instruction = instruction
        self.config = config or DirectConfig()
        self.description = episode.description.environment
        self.representation = REPRESENTATIONS[self.config.representation].bind(self.description)
        self.joint_space = self.representation.space == "joint"
        self.horizon = (self.config.min_horizon, self.config.max_horizon)
        self.limits = Limits(self.config.translation_limit_m, self.config.rotation_limit_rad,
                             self.config.joint_step_limit_rad)
        self.arm_names = tuple(arm.name for arm in self.description.arms)
        self.cameras = [camera.name for camera in self.description.cameras]
        self.root = Path(output_dir) / "direct"
        (self.root / "requests").mkdir(parents=True, exist_ok=True)
        self.frames: list[dict] = []
        self.cancelled = threading.Event()
        # Monotonic time after which no new inference starts (set by the caller's episode budget).
        self.deadline: float | None = None
        self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "requests": 0}

    # -- observation ----------------------------------------------------------

    def _measured(self, arm: dict) -> dict:
        pose = arm["control_pose"]
        measured = {"ee_pos": [round(v, 5) for v in pose["position"]],
                    "ee_quat": [round(v, 5) for v in pose["quaternion"]],
                    "gripper_closure": round(1 - arm["gripper"], 4),
                    "gripper_command": round(1 - arm["gripper_command"], 4)}
        if self.joint_space:
            measured["joints"] = [round(v, 5) for v in arm.get("joints", [])]
        return measured

    def _record_frame(self, snapshot: dict) -> dict:
        images = self.episode.image_paths(snapshot)
        measured = {name: self._measured(snapshot["state"]["arms"][name]) for name in self.arm_names}
        frame = {"step": int(snapshot["state"]["native_steps"]), "captured_at": snapshot.get("captured_at"),
                 "state_version": snapshot["state_version"],
                 "images": {name: self._relative(path) for name, path in images.items()},
                 "proprio": {"arms": measured} if len(measured) > 1 else next(iter(measured.values()))}
        self.frames.append(frame)
        _jsonl(self.root / "frames.jsonl", frame)
        return frame

    def _relative(self, path) -> str:
        """Run-relative paths keep records valid when a run directory is moved or copied."""
        path = Path(path).resolve()
        try:
            return str(path.relative_to(self.root.parent.resolve()))
        except ValueError:
            return str(path)

    def _absolute(self, path: str) -> str:
        return str(self.root.parent / path) if not Path(path).is_absolute() else path

    def _capture(self) -> dict:
        snapshot = self.episode.env.capture(CaptureRequest(cameras=self.cameras, high_resolution=False))
        self.episode.latest_snapshot = snapshot
        return self._record_frame(snapshot)

    def _history(self) -> list[dict]:
        window = self.frames[-self.config.history_length:]
        return list(reversed(window[::-1][::self.config.history_stride]))

    # -- request --------------------------------------------------------------

    def _messages(self, system: str, history: list[dict]) -> tuple[list[dict], list[dict]]:
        """Messages for the provider and the same messages with images as file references."""
        current = history[-1]
        proprio = [{"frame": i, "control_step": f["step"], **f["proprio"]} for i, f in enumerate(history)]
        text = (f"Observation history window: {self.config.history_length} control step(s); "
                f"sample stride {self.config.history_stride}.\nInstruction: {self.instruction}\n"
                f"This request contains the latest {len(history)} chronological observation(s), oldest first. "
                f"Camera order per frame: {self.cameras}.\n"
                f"Synchronized proprioception: {json.dumps(proprio, separators=(',', ':'))}")
        live, stored = [{"type": "text", "text": text}], [{"type": "text", "text": text}]
        for index, frame in enumerate(history):
            for camera in self.cameras:
                label = {"type": "text", "text": f"Observation {index + 1}/{len(history)}, control step "
                                                 f"{frame['step']}; Camera: {camera}"}
                path = frame["images"][camera]
                live += [label, {"type": "image_url", "image_url": {
                    "url": _data_url(self._absolute(path), self.config.image_max_side, self.config.jpeg_quality)}}]
                stored += [label, {"$evoab_image": path}]
        head = [{"role": "system", "content": system}, {"role": "user", "content": f"Goal: {self.instruction}"}]
        return head + [{"role": "user", "content": live}], head + [{"role": "user", "content": stored}]

    def _decode(self, completion, measured: dict[str, Target]):
        calls = completion.tool_calls
        if completion.error:
            raise OutputError(f"provider error: {completion.error}")
        if len(calls) != 1:
            raise OutputError(f"expected exactly one {self.representation.tool} call, got {len(calls)}")
        call = calls[0]
        if call["name"] != self.representation.tool:
            raise OutputError(f"expected tool {self.representation.tool}, got {call['name']}")
        arguments = call["arguments"]
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError as error:
                raise OutputError(f"arguments are not valid JSON: {error}") from error
        targets = self.representation.decode_chunk(arguments, measured, self.horizon, self.limits)
        return arguments, targets

    # -- execution ------------------------------------------------------------

    def _step(self, targets: dict) -> dict:
        if self.joint_space:
            command = JointCommand(arms={name: JointTarget(joints=list(target.joints), gripper=1 - target.closure)
                                         for name, target in targets.items()})
        else:
            command = PoseCommand(solver="native", arms={
                name: ArmCommand(pose=Pose(position=target.position, quaternion=target.quaternion),
                                 gripper=1 - target.closure)
                for name, target in targets.items()
            })
        reply = self.episode.env.step(StepRequest(command=command,
                                                  expected_state_version=self.episode.state.state_version))
        self.episode.accept_primitive(reply)
        if reply.execution_status != "completed":
            raise RuntimeError(f"Control step {reply.execution_status}: {reply.feedback.get('diagnostics')}")
        return self._capture()

    def _target(self, proprio: dict, closure: float | None = None):
        closure = proprio["gripper_command"] if closure is None else closure
        if self.joint_space:
            return JointRow(tuple(proprio["joints"]), closure)
        return Target(tuple(proprio["ee_pos"]), tuple(proprio["ee_quat"]), closure)

    def run(self) -> DirectResult:
        started = time.time()
        result = DirectResult()
        self._capture()
        initial = self.frames[-1]
        start_step = initial["step"]
        snapshot = self.episode.latest_snapshot
        system, blocks = prompts.assemble(
            description=self.description, representation=self.representation,
            horizon=self.horizon, limits=self.limits, blocks=self.config.blocks,
            initial_state=initial["proprio"], cameras=snapshot.get("cameras", {}),
            robot_text=self.config.robot_prompt, custom_prompt=self.config.custom_prompt)
        tools = [self.representation.schema(self.horizon, self.arm_names)]
        (self.root / "prompt.md").write_text(system + "\n", encoding="utf-8")
        (self.root / "policy.json").write_text(json.dumps({
            "policy": "direct", "integration": self.integration, "arms": self.arm_names,
            "representation": self.representation.name,
            "representation_label": self.representation.label, "tool": self.representation.tool,
            "columns": list(self.representation.columns), "horizon": list(self.horizon),
            "joint_names": {k: list(v) for k, v in getattr(self.representation, "joint_names", {}).items()},
            "history_length": self.config.history_length, "history_stride": self.config.history_stride,
            "limits": {"translation_m": self.limits.translation_m, "rotation_rad": self.limits.rotation_rad,
                       "joint_rad": self.limits.joint_rad},
            "repair_attempts": self.config.repair_attempts, "control_hz": self.description.control_frequency_hz,
            "max_episode_steps": self.description.max_episode_steps, "cameras": self.cameras,
            "instruction": self.instruction, "model": self.model.describe(), "blocks": blocks,
            "tools": tools, "started_at": started,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        index = 0
        while not self.cancelled.is_set():
            current = self.frames[-1]
            if self.episode.state.ended or current["step"] >= self.description.max_episode_steps:
                result.stop_reason = "episode_ended"
                break
            if self.config.max_calls and index >= self.config.max_calls:
                result.stop_reason = "call_limit"
                break
            if self.deadline is not None and time.monotonic() >= self.deadline:
                result.stop_reason = "timeout"
                break
            index += 1
            record = self._inference(index, system, tools)
            _jsonl(self.root / "calls.jsonl", record)
            logger.info("Inference %d: %.1fs, %d attempt(s), %d step(s)%s; %s", index, record["api_latency_s"],
                        record["output_attempts"], record["horizon"], " [safe hold]" if record["safe_hold"] else "",
                        record["note"][:160])
            result.calls = index
            if record["error"]:
                result.stop_reason = record["failure_kind"]
                result.error = record["error"]
                break
            if record.get("execution_error"):
                result.stop_reason, result.error = "execution_error", record["execution_error"]
                break
        else:
            result.stop_reason = "cancelled"
        # Steps this run executed; a delegated run starts mid-episode.
        result.steps = self.frames[-1]["step"] - start_step
        result.usage = dict(self.usage)
        result.elapsed_s = time.time() - started
        return result

    def _inference(self, index: int, system: str, tools: list[dict]) -> dict:
        build_started = time.perf_counter()
        history = self._history()
        current = history[-1]
        messages, stored = self._messages(system, history)
        request_build_s = time.perf_counter() - build_started
        request_path = self.root / "requests" / f"call_{index:04d}.json"
        attempts, arguments, targets, error = [], None, None, None
        proprio = current["proprio"]
        if len(self.arm_names) == 1:
            name, = self.arm_names
            measured = {name: self._target(proprio)}
        else:
            measured = {name: self._target(proprio["arms"][name]) for name in self.arm_names}
        for attempt in range(1 + self.config.repair_attempts):
            completion = self.model.complete(messages, tools=tools, tool_choice="required")
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                self.usage[key] += int(completion.usage.get(key) or 0)
            self.usage["requests"] += 1
            entry = {"attempt": attempt + 1, "started_at": completion.started_at,
                     "latency_s": round(completion.latency_s, 3), "finish_reason": completion.finish_reason,
                     "usage": completion.usage, "content": completion.content, "reasoning": completion.reasoning,
                     "tool_calls": completion.tool_calls}
            try:
                arguments, targets = self._decode(completion, measured)
                attempts.append(entry)
                error = None
                break
            except OutputError as failure:
                error = str(failure)
                entry["error"] = error
                attempts.append(entry)
                entry["failure_kind"] = "provider_error" if completion.error else "invalid_output"
                if completion.error or attempt == self.config.repair_attempts:
                    continue
                messages = messages + [{"role": "user", "content": (
                    f"Your previous reply was rejected: {error}. Nothing was executed. Respond again with exactly "
                    f"one {self.representation.tool} tool call for the same observation.")}]
                stored = stored + [messages[-1]]
        request_path.write_text(json.dumps({"messages": stored, "tools": tools, "tool_choice": "required",
                                            "model": self.model.describe()}, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        targets = targets or []
        target_rows = [{name: target.row() for name, target in row.items()} for row in targets]
        if len(self.arm_names) == 1:
            target_rows = [next(iter(row.values())) for row in target_rows]
        clamped = [any(t.clamped for t in row.values()) for row in targets]
        record = {
            "index": index, "start_step": current["step"], "observed_steps": [f["step"] for f in history],
            "requested_at": attempts[0]["started_at"], "request_build_s": round(request_build_s, 4),
            "api_latency_s": round(sum(a["latency_s"] for a in attempts), 3), "attempts": attempts,
            "output_attempts": len(attempts), "error": error, "failure_kind": attempts[-1].get("failure_kind"), "safe_hold": False,
            "tool": self.representation.tool, "arguments": arguments,
            "note": (arguments or {}).get("note", ""), "request": str(request_path.relative_to(self.root.parent)),
            "targets": target_rows, "clamped": clamped,
            "safety_clamped": any(clamped), "executed": [],
        }
        execution_started = time.perf_counter()
        for row, target in enumerate(targets):
            if self.cancelled.is_set() or self.episode.state.ended:
                break
            try:
                frame = self._step(target)
            except Exception as failure:  # noqa: BLE001 - report and stop the loop
                logger.exception("Direct step failed")
                record["execution_error"] = f"{type(failure).__name__}: {failure}"
                break
            record["executed"].append({"row": row, "step": frame["step"], "at": frame["captured_at"],
                                       "measured": frame["proprio"]})
        record["horizon"] = len(record["executed"])
        record["execution_s"] = round(time.perf_counter() - execution_started, 3) if targets else 0.0
        return record

    def cancel(self) -> None:
        self.cancelled.set()
