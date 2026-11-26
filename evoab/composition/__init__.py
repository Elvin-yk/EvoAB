"""Composition: recipes, the component catalog, the method library and their compilation.

See "Methods and recipes" in docs/guide.md for the recipe format and adding methods.
"""

from evoab.composition.recipe import Recipe, Role, read_recipe_file, with_overrides

__all__ = ["Recipe", "Role", "read_recipe_file", "with_overrides"]
