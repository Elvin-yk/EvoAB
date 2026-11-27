"""Configuration for the Direct actor service."""

from pydantic import BaseModel, ConfigDict, Field

from evoab.direct.actor import absolute_actor_profile


class DirectActorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    model_preset: str = Field(min_length=1)
    arms: list[str] = Field(min_length=1)
    cameras: list[str] = Field(min_length=1)
    control_frequency_hz: float = Field(gt=0)
    horizon: int = Field(default=3, ge=1, le=15, strict=True)

    def profile(self):
        if any(not arm.strip() for arm in self.arms):
            raise ValueError("Direct actor arm names must be nonempty")
        return absolute_actor_profile(self.arms, self.cameras, self.control_frequency_hz, self.horizon)


def resolve_config(settings):
    return DirectActorConfig.model_validate(settings)


def service_limits(config):
    return {"max_batch_size": 1}
