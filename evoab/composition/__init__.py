"""Composition: recipes, the component catalog, the method library and their compilation.

See docs/composition.md for the recipe format and docs/methods.md for adding methods.
"""

from evoab.composition.recipe import Recipe, Role, read_recipe_file, with_overrides

__all__ = ["Recipe", "Role", "read_recipe_file", "with_overrides"]
