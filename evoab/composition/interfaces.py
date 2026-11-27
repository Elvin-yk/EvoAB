"""Named service contracts and checked, declarative calls between them."""
from __future__ import annotations

from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Endpoint(Contract):
    description: str = ""
    url: str

    @field_validator("url")
    @classmethod
    def address(cls, value):
        address = urlsplit(value)
        if (address.scheme not in {"http", "https"} or not address.hostname or
                address.username is not None or address.password is not None or address.query or address.fragment):
            raise ValueError("Use an absolute HTTP(S) URL without credentials, query or fragment")
        return value


def checked_schema(value):
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as error:
        raise ValueError(f"Invalid JSON Schema: {error.message}") from error
    return value


class HttpAPI(Endpoint):
    kind: Literal["http"]
    method: Literal["GET", "POST"] = "POST"
    input_schema: dict
    output_schema: dict
    bearer_env: str | None = None
    timeout_s: float = Field(default=30, gt=0, allow_inf_nan=False)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def valid_schema(cls, value):
        return checked_schema(value)

    @model_validator(mode="after")
    def object_input(self):
        if self.input_schema.get("type") != "object":
            raise ValueError("HTTP tool input_schema must describe a JSON object")
        return self


class PolicyAPI(Endpoint):
    kind: Literal["policy"]
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding: str | None = None
    review: bool | None = None
    review_model: str | None = None


class Select(Contract):
    source: str
    choices: dict[str, Name] = Field(min_length=1)


class Step(Contract):
    id: Name
    call: Name | Select
    args: dict[str, Any] = Field(default_factory=dict)

    def targets(self):
        return [self.call] if isinstance(self.call, str) else list(self.call.choices.values())


def references(value):
    if isinstance(value, dict):
        if set(value) == {"from"}:
            yield value["from"]
        else:
            for item in value.values():
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)


def resolve(path, state):
    value = state
    for key in path.split("."):
        if isinstance(value, list) and key.isdecimal() and int(key) < len(value):
            value = value[int(key)]
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise ValueError(f"No value at {path!r}")
    return value


def arguments(value, state):
    if isinstance(value, dict):
        if set(value) == {"from"}:
            return resolve(value["from"], state)
        return {key: arguments(item, state) for key, item in value.items()}
    if isinstance(value, list):
        return [arguments(item, state) for item in value]
    return value
