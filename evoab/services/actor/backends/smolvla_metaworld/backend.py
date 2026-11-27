"""Serve the public LeRobot MT50 checkpoint through the shared actor API."""

import hashlib

from evoab.services.actor.contracts import ActorInfo, NumericChunk
from evoab.services.actor.images import decode_image
from evoab.services.actor.metaworld_profile import POLICY_CAMERA, actor_profile

from .config import SmolVLAMetaWorldConfig

MODEL = "lerobot/smolvla_metaworld"
CAMERA_FEATURE = "observation.images.camera1"
RUNTIME = "lerobot_smolvla"


class SmolVLAMetaWorldBackend:
    def __init__(self, config: SmolVLAMetaWorldConfig):
        from importlib.metadata import version

        import torch
        from lerobot.configs.policies import PreTrainedConfig
        from lerobot.policies.factory import make_pre_post_processors
        from lerobot.policies.smolvla.configuration_smolvla import SmolVLAConfig
        from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy

        checkpoint = config.checkpoint.expanduser().resolve(strict=True)
        vlm_config = config.vlm_config.expanduser().resolve(strict=True)
        with (checkpoint / "model.safetensors").open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != config.checkpoint_sha256:
                raise ValueError("SmolVLA checkpoint SHA-256 differs from the configured identity")
        policy_config = PreTrainedConfig.from_pretrained(checkpoint, local_files_only=True)
        if not isinstance(policy_config, SmolVLAConfig):
            raise ValueError("The MetaWorld actor requires a SmolVLA checkpoint")
        inputs, outputs = policy_config.input_features, policy_config.output_features
        if (set(inputs) != {"observation.state", CAMERA_FEATURE}
                or tuple(inputs["observation.state"].shape) != (4,)
                or tuple(inputs[CAMERA_FEATURE].shape) != (3, 480, 480)
                or set(outputs) != {"action"} or tuple(outputs["action"].shape) != (4,)
                or policy_config.n_obs_steps != 1):
            raise ValueError("SmolVLA checkpoint does not declare the MetaWorld observation and action profile")

        policy_config.device = config.device
        policy_config.vlm_model_name = str(vlm_config)
        # All learned parameters are in the policy checkpoint. Require a complete load.
        policy_config.load_vlm_weights = False
        self._policy = SmolVLAPolicy.from_pretrained(
            checkpoint, config=policy_config, local_files_only=True, strict=True,
        )
        if config.device == "cuda":
            self._policy.model.vlm_with_expert.vlm.to(dtype=torch.bfloat16)
        self._preprocess, self._postprocess = make_pre_post_processors(
            policy_config, pretrained_path=str(checkpoint),
            preprocessor_overrides={
                "device_processor": {"device": config.device},
                "tokenizer_processor": {"tokenizer_name": str(vlm_config)},
            },
        )
        self._config = config
        self._torch = torch
        self._noise_shape = (1, policy_config.chunk_size, policy_config.max_action_dim)
        identity = hashlib.sha256(
            f"{MODEL}:{config.checkpoint_sha256}:{config.device}:{config.policy_rng_seed}:{version('lerobot')}".encode()
        )
        # Normalization statistics and the tokenizer can change actions with the same weights.
        policy_files = sorted((*checkpoint.glob("*.json"), *checkpoint.glob("*processor*.safetensors")))
        vlm_files = sorted((*vlm_config.glob("*.json"), *vlm_config.glob("*.txt")))
        for path in (*policy_files, *vlm_files):
            identity.update(path.name.encode())
            with path.open("rb") as stream:
                identity.update(hashlib.file_digest(stream, "sha256").digest())
        self.info = ActorInfo(
            model=MODEL, runtime=RUNTIME, model_id=identity.hexdigest(),
            checkpoint_sha256=config.checkpoint_sha256, model_config_name="mt50",
            profile=actor_profile(policy_config.chunk_size), policy_rng_seed=config.policy_rng_seed,
        )

    def create_session(self):
        self._policy.reset()
        return self._torch.Generator(device=self._config.device).manual_seed(self._config.policy_rng_seed)

    def close_session(self, state):
        self._policy.reset()

    def predict_batch(self, requests):
        if len(requests) != 1:
            raise ValueError("The MetaWorld SmolVLA backend admits one inference at a time")
        request = requests[0]
        observation = request.observation
        torch = self._torch
        rgb = decode_image(observation.images[POLICY_CAMERA])
        with torch.inference_mode():
            batch = self._preprocess({
                CAMERA_FEATURE: torch.from_numpy(rgb.copy()).permute(2, 0, 1).float().div_(255),
                "observation.state": torch.tensor(observation.state, dtype=torch.float32),
                "task": observation.instruction,
            })
            noise = torch.randn(self._noise_shape, device=self._config.device, generator=request.state)
            normalized = self._policy.predict_action_chunk(batch, noise=noise)
            actions = self._postprocess(normalized).squeeze(0)
            if not torch.isfinite(actions).all().item():
                raise ValueError("SmolVLA predicted a non-finite MetaWorld action")
            return [NumericChunk(actions=actions.clamp(-1, 1).tolist())]

    def close(self):
        self._policy = None
