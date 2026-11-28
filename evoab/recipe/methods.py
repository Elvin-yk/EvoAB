"""The method library: recipes with reproduction cards, discoverable by id.

Each method is a directory holding ``method.yaml`` (the recipe), ``CARD.md``
(paper, settings, our numbers versus the paper's, known differences) and any
prompt files the recipe references. Built-in methods ship in ``evoab/methods``;
``EVOAB_METHODS_DIR`` adds further roots (``os.pathsep``-separated), whose
methods replace built-ins with the same id.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from evoab.recipe.schema import Recipe, read_recipe_file

BUILTIN_ROOT = Path(__file__).resolve().parents[1] / "methods"
RECIPE_FILE = "method.yaml"
CARD_FILE = "CARD.md"


@dataclass(frozen=True)
class Method:
    recipe: Recipe
    root: Path | None
    source: str

    @property
    def id(self) -> str:
        return self.recipe.id

    @property
    def card(self) -> str:
        path = self.root / CARD_FILE if self.root else None
        return path.read_text(encoding="utf-8") if path and path.is_file() else ""


def method_roots() -> list[tuple[Path, str]]:
    roots = [(BUILTIN_ROOT, "builtin")]
    for entry in filter(None, os.environ.get("EVOAB_METHODS_DIR", "").split(os.pathsep)):
        roots.append((Path(entry).expanduser().resolve(), "user"))
    return roots


def _scan(root: Path, source: str) -> dict[str, Method]:
    found: dict[str, Method] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.glob(f"*/{RECIPE_FILE}")):
        recipe = read_recipe_file(path)
        if recipe.id != path.parent.name:
            raise ValueError(f"{path}: id {recipe.id!r} must match its directory name {path.parent.name!r}")
        found[recipe.id] = Method(recipe, path.parent, source)
    return found


def list_methods() -> list[Method]:
    methods: dict[str, Method] = {}
    for root, source in method_roots():
        methods.update(_scan(root, source))
    order = {"builtin": 0, "reproduction": 1, "experimental": 2}
    return sorted(methods.values(), key=lambda m: (order[m.recipe.status], m.id))


def get_method(reference: str) -> Method:
    """A method by id, or a recipe file / method directory on disk.

    Library ids win; a reference is read from disk only when it looks like a path
    (a separator or a recipe suffix) or names no library method.
    """
    path = Path(reference).expanduser()
    looks_like_path = "/" in reference or path.suffix in {".yaml", ".yml", ".json"}
    if not looks_like_path:
        for method in list_methods():
            if method.id == reference:
                return method
    if looks_like_path or path.is_dir():
        file = path / RECIPE_FILE if path.is_dir() else path
        if not file.is_file():
            raise ValueError(f"recipe file {file} does not exist")
        return Method(read_recipe_file(file), file.parent.resolve(), "file")
    available = ", ".join(m.id for m in list_methods())
    raise ValueError(f"unknown method {reference!r}; available: {available}")
