"""User-defined tools: a decorated Python function is an agent tool.

    from evoab import tool

    @tool
    def lift_by(robot, arm: str, height_m: float) -> str:
        \"\"\"Raise one arm's grasp site by height_m metres and report where it ended.\"\"\"
        state = robot.view_env_state()
        ...
        return "lifted"

A recipe selects a file of such functions with the ``user_tools`` capability,
``{id: user_tools, options: {file: my_tools.py}}``. The docstring is the
description the model sees and the type hints are the argument schema. The first
parameter, ``robot``, calls the episode's other tools through the same executor
the agent uses, so scheduling, receipts and records are unchanged. A tool returns
text, a dict, a PIL image, a ``ToolOutput``, or a ``(text_or_dict, [images])`` pair.
"""
from __future__ import annotations

import importlib.util
import inspect
import io
import types
import typing
from pathlib import Path
from typing import Any, Callable, Literal, get_args, get_origin

from evoab.tools.toolkit import ImageAttachment, ToolOutput, ToolProvider, ToolSpec, bind_tools

_MARK = "__evoab_tool__"
_SCALARS = {str: "string", int: "integer", float: "number", bool: "boolean"}


def tool(function: Callable | None = None, *, name: str | None = None, readonly: bool = False):
    """Mark a function as an agent tool; use bare (``@tool``) or with options."""
    def mark(fn: Callable) -> Callable:
        setattr(fn, _MARK, {"name": name or fn.__name__, "readonly": readonly})
        return fn
    return mark(function) if function is not None else mark


def load_tools(path: str | Path) -> list[Callable]:
    """Import a tool file and return its ``@tool`` functions; fails on a file without any."""
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"user tool file not found: {path}")
    module_name = "evoab_user_tools_" + format(abs(hash(str(path))), "x")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot import user tool file {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    functions = [value for value in vars(module).values() if callable(value) and hasattr(value, _MARK)]
    if not functions:
        raise ValueError(f"{path} defines no @tool functions")
    names = [getattr(fn, _MARK)["name"] for fn in functions]
    if len(set(names)) != len(names):
        raise ValueError(f"{path} defines duplicate tool names")
    for fn in functions:
        tool_spec(fn)  # validate signatures and docstrings before any episode starts
    return functions


def tool_spec(fn: Callable) -> ToolSpec:
    """The model-facing spec of one ``@tool`` function."""
    meta = getattr(fn, _MARK)
    description = inspect.getdoc(fn)
    if not description:
        raise ValueError(f"tool {meta['name']!r} needs a docstring: it is the description the model sees")
    parameters = list(inspect.signature(fn).parameters.values())
    if not parameters or parameters[0].name != "robot":
        raise ValueError(f"tool {meta['name']!r}: the first parameter must be `robot`")
    hints = typing.get_type_hints(fn)
    properties, required = {}, []
    for parameter in parameters[1:]:
        if parameter.kind not in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY):
            raise ValueError(f"tool {meta['name']!r}: *args and **kwargs are not supported")
        if parameter.name not in hints:
            raise ValueError(f"tool {meta['name']!r}: parameter {parameter.name!r} needs a type hint")
        schema, optional = _schema(hints[parameter.name], f"{meta['name']}.{parameter.name}")
        if parameter.default is not parameter.empty:
            schema["default"] = parameter.default
        elif not optional:
            required.append(parameter.name)
        properties[parameter.name] = schema
    return ToolSpec(meta["name"], description,
                    {"type": "object", "properties": properties, "required": required,
                     "additionalProperties": False}, readonly=meta["readonly"])


def _schema(hint: Any, where: str) -> tuple[dict, bool]:
    origin, args = get_origin(hint), get_args(hint)
    if origin in (typing.Union, types.UnionType) and type(None) in args:
        rest = [arg for arg in args if arg is not type(None)]
        if len(rest) == 1:
            return _schema(rest[0], where)[0], True
    if hint in _SCALARS:
        return {"type": _SCALARS[hint]}, False
    if origin is Literal:
        return {"enum": list(args)}, False
    if hint is list or origin is list:
        return ({"type": "array", "items": _schema(args[0], where)[0]} if args else {"type": "array"}), False
    if hint is dict or origin is dict:
        return {"type": "object"}, False
    raise ValueError(f"{where}: unsupported type {hint!r}; use str, int, float, bool, Literal, list or dict")


class ToolError(RuntimeError):
    """A tool called through ``robot`` failed; ``output`` holds its full result."""

    def __init__(self, name: str, output: ToolOutput):
        super().__init__(f"{name} failed: {output.error or output.to_dict()}")
        self.output = output


class Robot:
    """The episode's tools as methods: ``robot.move(...)`` returns the result as a dict."""

    def __init__(self, call: Callable[[str, dict], ToolOutput], names: set[str]):
        self._call, self._names = call, names

    def call(self, name: str, **arguments: Any) -> dict:
        if name not in self._names:
            raise AttributeError(f"no tool {name!r}; available: {sorted(self._names)}")
        output = self._call(name, arguments)
        if not output.success or output.error:
            raise ToolError(name, output)
        return {**output.to_dict(), "images": output.images}

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda **arguments: self.call(name, **arguments)


class UserTools(ToolProvider):
    """The ``@tool`` functions of one file, bound to an episode."""

    provider_id = "user_tools"

    def __init__(self, episode, functions: list[Callable]):
        self.episode = episode
        self._functions = {getattr(fn, _MARK)["name"]: fn for fn in functions}
        self._executor = None

    def bind_executor(self, executor) -> None:
        """Late binding: the executor exists only after every provider's tools are known."""
        self._executor = executor

    def _robot(self) -> Robot:
        if self._executor is None:
            raise RuntimeError("user tools are not bound to an executor")
        names = {spec["name"] for spec in self._executor.get_tool_schemas()} - set(self._functions)

        def call(name: str, arguments: dict) -> ToolOutput:
            result = self._executor.call(name, arguments)
            if result.output is None:
                raise RuntimeError(f"{name} did not complete ({result.status})")
            return result.output
        return Robot(call, names)

    def _handler(self, fn: Callable) -> Callable:
        def handle(**arguments) -> ToolOutput:
            try:
                return _output(fn(self._robot(), **arguments))
            except ToolError as error:
                return ToolOutput(success=False, error=str(error), extra=error.output.extra)
            except Exception as error:  # noqa: BLE001 - user code errors are tool results.
                return ToolOutput(success=False, error=f"{type(error).__name__}: {error}")
        return handle

    def get_tools(self):
        specs = [tool_spec(fn) for fn in self._functions.values()]
        return bind_tools(specs, provider_id=self.provider_id,
                          handlers={name: self._handler(fn) for name, fn in self._functions.items()})


def _output(value: Any) -> ToolOutput:
    if isinstance(value, ToolOutput):
        return value
    images: list = []
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], (list, tuple)):
        value, images = value
    elif _is_image(value):
        value, images = None, [value]
    extra = {} if value is None else value if isinstance(value, dict) else {"result": str(value)}
    return ToolOutput(extra=dict(extra), images=[_attachment(image) for image in images])


def _is_image(value: Any) -> bool:
    return isinstance(value, ImageAttachment) or type(value).__module__.startswith("PIL.")


def _attachment(image: Any) -> ImageAttachment:
    if isinstance(image, ImageAttachment):
        return image
    if not _is_image(image):
        raise TypeError("tool images must be PIL images or ImageAttachment")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return ImageAttachment(buffer.getvalue(), "image/png")


def resolve_file(file: str, base_dir: Path | None) -> Path:
    """A recipe's tool file: relative to the method directory when it exists there, else the working directory."""
    path = Path(file).expanduser()
    if not path.is_absolute() and base_dir is not None and (Path(base_dir) / path).is_file():
        path = Path(base_dir) / path
    return path.resolve()
