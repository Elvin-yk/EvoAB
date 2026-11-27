"""Predict action chunks with Direct's model-output contract."""

import hashlib
import json

from evoab.direct.actions import AbsoluteEEF, Limits, Target, decode_completion
from evoab.direct.prompts import timing_text
from evoab.provider.catalog import ModelClient
from evoab.services.actor.contracts import ActorInfo, NumericChunk
from evoab.services.actor.images import decode_image


class DirectBackend:
    def __init__(self, config):
        self.config = config
        self.representation = AbsoluteEEF()
        self.horizon = (config.horizon, config.horizon)
        self.limits = Limits()
        profile = config.profile()
        self.tools = [self.representation.schema(self.horizon, tuple(config.arms))]
        self.system = ("Control the robot using the goal, current camera images and measured grasp-site poses. "
                       "Use these arm names: " + ", ".join(config.arms) + ".\n\n"
                       + timing_text(config.control_frequency_hz, self.limits) + "\n\n"
                       + self.representation.instruction(self.horizon))
        if len(config.arms) > 1:
            self.system += ("\nEach action row is an object keyed by arm name. "
                            "Active arm targets execute together in one control interval.")
        self.model = ModelClient(config.model_preset)
        identity = {"backend": "direct", "profile": profile.model_dump(mode="json"),
                    "preset": self.model.describe(), "system": self.system, "tools": self.tools}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        # Hosted weights cannot be hashed. The actor protocol pins the deployment configuration.
        self.info = ActorInfo(model=self.model.model, runtime="direct", model_id=fingerprint,
                              checkpoint_sha256=fingerprint, model_config_name=config.model_preset,
                              profile=profile)

    def create_session(self):
        return None  # Each request conditions on the supplied observation only.

    def close_session(self, state):
        pass

    def predict_batch(self, requests):
        work, = requests
        return [self._predict(work.observation)]

    def _predict(self, observation):
        arms = tuple(self.config.arms)
        width = self.representation.width
        measured = {}
        for index, name in enumerate(arms):
            row = observation.state[index * width:(index + 1) * width]
            measured[name] = Target(tuple(row[:3]), tuple(row[3:7]), 1 - row[7])
        poses = {name: {"ee_pos": target.position, "ee_quat": target.quaternion,
                        "gripper_closure": target.closure} for name, target in measured.items()}
        content = [{"type": "text", "text": f"Goal: {observation.instruction}\nMeasured arms: {json.dumps(poses)}"}]
        for camera in self.config.cameras:
            image = observation.images[camera]
            decode_image(image)
            content.extend([{"type": "text", "text": f"Camera: {camera}"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image.data}"}}])
        completion = self.model.complete([{"role": "system", "content": self.system},
                                          {"role": "user", "content": content}],
                                         tools=self.tools, tool_choice="required")
        arguments, targets = decode_completion(completion, self.representation, measured, self.horizon, self.limits)
        actions = []
        for step in targets:
            values = []
            for arm in arms:
                target = step[arm]
                values.extend((*target.position, *target.quaternion, 1 - target.closure))
            actions.append(values)
        return NumericChunk(actions=actions, evidence={"note": arguments.get("note", "")})

    def close(self):
        self.model.close()
