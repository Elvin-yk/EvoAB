---
name: add-evoab-method
description: Add or reproduce an agent method in EvoAB as a recipe over tools, code and action. Use when asked to implement, reproduce or compose a robot-agent method (tool orchestration, direct action chunks, code as policy, multi-role delegation, cross-episode iteration) in this repository.
---

# Add an EvoAB method

A method is a recipe over the three ways of acting (`act`: tools, code, action)
and capabilities. `evoab/methods/` holds only `tools`, `code` and `action`;
reproductions are recipe files (see `examples/recipes/`). Read the
"Methods and recipes" section of `docs/guide.md` and the "Components" section
of `docs/architecture.md` before starting.

## Steps

1. Map the paper onto recipe axes. For each agent in the paper decide:
   - act: any combination of `tools` (calls robot tools), `code` (writes
     programs) and `action` (emits action chunks; alone it is stateless);
   - capabilities: `skills`, `scene`, `gripper`, `hybrid`, `episode`,
     `delegate`, `observation` (`evoab methods components --integration X`);
   - brain: an optional model preset and effort per role (every role runs on
     EvoAB's built-in agent).
   Multiple agents become roles connected with `delegate`. Cross-episode learning
   becomes `loop.episodes` with `carry: [programs]` or `[skills]`.
2. Copy the closest built-in `method.yaml` or example recipe into a new file.
3. Write method instructions in `prompt.append` or `prompts/*.md`. Do not edit
   integration prompts for a method.
4. Validate: `evoab methods validate <file> --integration <each target>`.
5. Check the composition offline:
   `PYTHONPATH=. python scripts/validation/recipe_composition.py` and
   `PYTHONPATH=. python scripts/validation/tool_composition.py`.
6. Record the paper and code links in `references`, and the paper's setting,
   what is reproduced and known differences as comments in the recipe.

## When the recipe cannot express the method

- New robot operation → a tool family in `evoab/tools/<family>/` and a
  `ToolModule` published by the integrations that support it.
- New action format → a `Representation` in `evoab/agent/act/action/actions.py`.
- New loop structure → extend `evoab/recipe/` (schema, compiler) and the
  session's role runner with tests; do not special-case a method id anywhere.

Never put credentials or machine paths in recipes.
