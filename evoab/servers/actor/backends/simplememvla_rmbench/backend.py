"""Run the reference SimpleMemVLA policy using captured simulator frames."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import numpy as np

from evoab.servers.actor.contracts import ActorInfo, NumericChunk
from evoab.servers.actor.images import decode_image
from evoab.servers.actor.rmbench_profile import (
    ACTION_HORIZON, CAMERAS, HISTORY_FRAMES, HISTORY_STRIDE, IMAGE_KEYS, JOINTS, NATIVE_FREQUENCY_HZ, REFERENCE_REVISION,
    actor_profile,
)

from .config import SimpleMemVLARMBenchConfig


@dataclass
class PolicySession:
    buffer: Any
    rng_state: Any
    last_step: int = -1
    latest_frames: dict | None = None


class SimpleMemVLARMBenchBackend:
    def __init__(self, config: SimpleMemVLARMBenchConfig):
        root = config.native_root.expanduser().resolve(strict=True)
        revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        if revision != REFERENCE_REVISION:
            raise ValueError(f"SimpleMemVLA must be at {REFERENCE_REVISION}, found {revision}")
        checkpoint = config.checkpoint.expanduser().resolve(strict=True)
        weight_digest = hashlib.sha256()
        with (checkpoint / "model.safetensors").open("rb") as weights:
            for block in iter(lambda: weights.read(8 * 1024 * 1024), b""):
                weight_digest.update(block)
        if weight_digest.hexdigest() != config.checkpoint_sha256:
            raise ValueError("SimpleMemVLA checkpoint SHA-256 differs from the configured identity")

        sys.path.insert(0, str(root))
        from rmbench_sim import eval_success

        if not Path(eval_success.__file__).resolve().is_relative_to(root):
            raise ValueError("A different SimpleMemVLA evaluator was imported")
        self.torch = eval_success.torch
        self.torch.manual_seed(config.policy_rng_seed)
        self.seed = config.policy_rng_seed
        options = SimpleNamespace(pretrained_checkpoint=str(checkpoint), compute_dtype="bfloat16",
                                  attn_implementation="flash_attention_2", num_denoising_steps=10,
                                  eval_temperature=1.0, max_subtask_tokens=64, pipelined=False)
        self.batched, self.buffer_factory, self.normalize_state = eval_success.build_policy(options)
        probe = self.buffer_factory()
        if (tuple(probe.image_keys) != IMAGE_KEYS or list(probe.history_image_keys) != [IMAGE_KEYS[0]]
                or probe.n_frames != HISTORY_FRAMES or probe.stride != HISTORY_STRIDE
                or probe.native_fps != NATIVE_FREQUENCY_HZ or probe.action_dim != JOINTS
                or probe.action_horizon != ACTION_HORIZON or self.normalize_state is None):
            raise ValueError("The checkpoint does not match the RMBench policy profile")
        identity = hashlib.sha256(f"{revision}:{config.checkpoint_sha256}:{self.seed}".encode())
        for file in sorted(checkpoint.iterdir()):
            if file.is_file() and file.suffix in {".json", ".jinja"}:
                identity.update(file.name.encode())
                identity.update(hashlib.sha256(file.read_bytes()).digest())
        self.info = ActorInfo(
            model="keithyc/simplememvla_rmbench", runtime="simplememvla", model_id=identity.hexdigest(),
            checkpoint_sha256=config.checkpoint_sha256, model_config_name="rmbench_aloha_dense",
            profile=actor_profile(), policy_rng_seed=self.seed,
        )

    def create_session(self):
        rng = self.torch.Generator(device=self.batched.device).manual_seed(self.seed)
        return PolicySession(self.buffer_factory(), rng.get_state())

    def close_session(self, state):
        state.buffer.reset()

    @staticmethod
    def observe(state, observation):
        history = observation.history[CAMERAS[0]]
        ticks = [frame.state_version for frame in history]
        if (ticks[-1] < state.last_step or any(tick % HISTORY_STRIDE for tick in ticks)
                or any(right - left != HISTORY_STRIDE for left, right in zip(ticks, ticks[1:]))):
            raise ValueError("RMBench observations must follow the native history cadence")
        new_frames = [frame for frame in history if frame.state_version > state.last_step]
        if state.last_step >= 0 and new_frames and new_frames[0].state_version != state.last_step + HISTORY_STRIDE:
            raise ValueError("RMBench actor session has a gap in its native camera history")
        wrists = {key: decode_image(observation.images[camera]) for camera, key in zip(CAMERAS[1:], IMAGE_KEYS[1:])}
        for frame in new_frames:
            if state.latest_frames is not None:
                # The reference policy reads only sampled head ticks and the latest wrists.
                for _ in range(HISTORY_STRIDE - 1):
                    state.buffer.observe(state.latest_frames, placeholder=True)
            state.latest_frames = {IMAGE_KEYS[0]: decode_image(frame.image), **wrists}
            state.buffer.observe(state.latest_frames)
            state.last_step = frame.state_version

    def predict_batch(self, requests):
        if len(requests) != 1:
            raise ValueError("SimpleMemVLA RMBench admits one inference at a time")
        work, = requests
        self.observe(work.state, work.observation)
        torch = self.torch
        device = self.batched.device
        with torch.random.fork_rng(devices=[device]), torch.inference_mode():
            torch.cuda.set_rng_state(work.state.rng_state, device)
            processed = work.state.buffer._prepare_inputs(work.observation.instruction)
            state = self.normalize_state.normalize(torch.tensor(work.observation.state, dtype=torch.float32))
            (actions, _), = self.batched.generate_batch([processed], [state.unsqueeze(0).to(device)])
            work.state.rng_state = torch.cuda.get_rng_state(device)
        return [NumericChunk(actions=np.asarray(actions).tolist())]

    def close(self):
        self.batched = self.buffer_factory = self.normalize_state = None
