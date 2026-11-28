"""Predict action chunks with Direct's model-output contract."""

import hashlib
import json

from evoab.agent.act.action.actions import AbsoluteEEF, Limits, Target, decode_completion
from evoab.agent.act.action.actor import absolute_actor_profile
from evoab.agent.act.action.prompts import timing_text
from evoab.agent.models.catalog import ModelClient
from evoab.servers.actor.contracts import ActorInfo, NumericChunk


class DirectBackend:
    def __init__(self, model_preset, environment):
        self.arms = tuple(arm.name for arm in environment.arms)
        self.representation = AbsoluteEEF()
        self.horizon = (3, 3)
        self.limits = Limits()
        profile = absolute_actor_profile(self.arms, [camera.name for camera in environment.cameras],
                                         environment.control_frequency_hz, self.horizon[1])
        self.tools = [self.representation.schema(self.horizon, self.arms)]
        self.system = ("Control the robot using the goal, current camera images and measured grasp-site poses. "
                       "Use these arm names: " + ", ".join(self.arms) + ".\n\n"
                       + timing_text(environment.control_frequency_hz, self.limits) + "\n\n"
                       + self.representation.instruction(self.horizon))
        if len(self.arms) > 1:
            self.system += ("\nEach action row is an object keyed by arm name. "
                            "Active arm targets execute together in one control interval.")
        self.model = ModelClient(model_preset)
        identity = {"backend": "direct", "profile": profile.model_dump(mode="json"),
                    "preset": self.model.describe(), "system": self.system, "tools": self.tools}
        fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        # Hosted weights cannot be hashed. The actor protocol pins the deployment configuration.
        self.info = ActorInfo(model=self.model.model, runtime="direct", model_id=fingerprint,
                              checkpoint_sha256=fingerprint, model_config_name=model_preset,
                              profile=profile)

    def create_session(self):
        return None  # Each request conditions on the supplied observation only.

    def close_session(self, state):
        pass

    def predict_batch(self, requests):
        work, = requests
        return [self._predict(work.observation)]

    def _predict(self, observation):
        width = self.representation.width
        measured = {}
        for index, name in enumerate(self.arms):
            row = observation.state[index * width:(index + 1) * width]
            measured[name] = Target(tuple(row[:3]), tuple(row[3:7]), 1 - row[7])
        poses = {name: {"ee_pos": target.position, "ee_quat": target.quaternion,
                        "gripper_closure": target.closure} for name, target in measured.items()}
        content = [{"type": "text", "text": f"Goal: {observation.instruction}\nMeasured arms: {json.dumps(poses)}"}]
        for camera in self.info.profile.cameras:
            image = observation.images[camera]
            content.extend([{"type": "text", "text": f"Camera: {camera}"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image.data}"}}])
        completion = self.model.complete([{"role": "system", "content": self.system},
                                          {"role": "user", "content": content}],
                                         tools=self.tools, tool_choice="required")
        arguments, targets = decode_completion(completion, self.representation, measured, self.horizon, self.limits)
        actions = []
        for step in targets:
            values = []
            for arm in self.arms:
                target = step[arm]
                values.extend((*target.position, *target.quaternion, 1 - target.closure))
            actions.append(values)
        return NumericChunk(actions=actions, evidence={"note": arguments.get("note", "")})

    def close(self):
        self.model.close()
