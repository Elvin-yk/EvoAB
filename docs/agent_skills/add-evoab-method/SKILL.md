---
name: add-evoab-method
description: Add or reproduce an agent method in EvoAB as a recipe with a reproduction card. Use when asked to implement, reproduce or compose a robot-agent method (tool orchestration, direct action chunks, code as policy, multi-role delegation, cross-episode iteration) in this repository.
---

# Add an EvoAB method

A method is `evoab/methods/<id>/method.yaml` (a recipe) plus `CARD.md`. Read the
"Methods and recipes" section of `docs/guide.md` and the "Components" section
of `docs/architecture.md` before starting.

## Steps

1. Map the paper onto recipe axes. For each agent in the paper decide:
   - channel: `tools` (calls robot tools), `code` (writes programs), `action`
     (emits action chunks each inference);
   - capabilities: `skills`, `scene`, `gripper`, `hybrid`, `episode`,
     `delegate`, `observation` (`evoab methods components --integration X`);
   - brain: an optional model preset and effort per role (every role runs on
     EvoAB's built-in agent).
   Multiple agents become roles connected with `delegate`. Cross-episode learning
   becomes `loop.episodes` with `carry: [programs]` or `[skills]`.
2. Copy the closest built-in method directory and rename it; the directory name
   is the id.
3. Write method instructions in `prompt.append` or `prompts/*.md`. Do not edit
   integration prompts for a method.
4. Expose tunable values as `parameters` with labels (Studio renders them).
5. Validate: `evoab methods validate <id> --integration <each target>`.
6. Check the composition offline:
   `PYTHONPATH=. python scripts/validation/recipe_composition.py` and
   `PYTHONPATH=. python scripts/validation/tool_composition.py`.
7. Write `CARD.md`: links, the paper's setting and numbers, what is reproduced,
   known differences, an empty results table.

## When the recipe cannot express the method

- New robot operation → a tool family in `evoab/tools/<family>/` and a
  `ToolModule` published by the integrations that support it.
- New action format → a `Representation` in `evoab/agent/act/action/actions.py`.
- New loop structure → extend `evoab/recipe/` (recipe, compiler, session
  role runner) with tests; do not special-case a method id anywhere.

Never put credentials or machine paths in recipes or cards.
