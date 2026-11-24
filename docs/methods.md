# Method library

A method is a directory with a recipe and a reproduction card:

```text
evoab/methods/<id>/
  method.yaml     the recipe (see composition.md); id must equal <id>
  CARD.md         paper, setting, our numbers versus the paper's, known differences
  prompts/*.md    optional method prompt files referenced by the recipe
```

Built-in methods ship in `evoab/methods/`. `EVOAB_METHODS_DIR` adds more roots
(`os.pathsep`-separated); a method there replaces a built-in with the same id.
A recipe file or method directory can also be passed directly:
`--recipe ./my_method/` or `--recipe my_recipe.yaml`.

## Built-in methods

| Id | Paradigms | Channel | Roles | Notes |
|---|---|---|---|---|
| `tool_team` | orchestrate | tools, optional action delegate | configurable | Studio role cards and per-role tool selection |
| `agentic` | orchestrate | tools | 1 | Default; skills, scene, gripper, episode |
| `clean` | orchestrate | tools | 1 | Four tools, compact results, initial RGB observation |
| `direct_policy` | direct | action | 1 | Astranomy-style Direct; `abs_eef`, `delta_eef` or `joint_abs` |
| `code_as_policy` | code | code | 1 | Programs against the robot API |
| `agent_policy` | orchestrate, policy | tools | 1 | Hybrid actor collaboration; gripper where available |
| `rua` | orchestrate | tools | 1 | Paper setting of RUA (arXiv 2611.01560), native |
| `q0_preview_upstream` | orchestrate | tools | 1 | [Original Q0-Preview policy](../evoab/methods/q0_preview_upstream/CARD.md), RoboDojo simulation reference protocol |
| `rua_upstream` | orchestrate | tools | 1 | Original uni-robot-agent package as external evaluator, RoboDojo only |
| `planner_direct` | orchestrate, direct | tools → action | 2 | Planner delegates sub-goals to a Direct controller |
| `code_iterate` | code | code | 1 | Program library carried across episodes (RAX-style, partial) |

Any agent runs any method (except methods pinned to an external evaluator).
`evoab methods list [--integration X]` shows which are usable where; `evoab methods show ID` prints the card, recipe and parameters.

## Adding a method

1. Pick the closest built-in method and copy its directory.
2. Edit `method.yaml`: roles, channels, capabilities, loop, execution. Declare
   `parameters` for values a launcher should expose. The CLI takes `--set name=value`.
3. Put method instructions in `prompt.append` or `prompts/*.md`, not in an
   integration. Prompts describe the method; environments describe facts.
4. Write `CARD.md`: the paper and code links, the paper's setting and numbers,
   what this recipe reproduces and what it does not, and a results table.
5. Validate offline against every integration you target:
   `evoab methods validate <id-or-path> --integration robodojo`.
6. Run one episode and check `recipe.json`, `tools.json`, `effective_prompt.md`
   (and `prompts/<role>.md` for delegated roles).

If the method needs a capability that does not exist, add a tool module rather
than special-casing the method: put the tools in `evoab/tools/<family>/`, publish
a `ToolModule` (with `tools`, `options` and `label`) from the integrations that
support it, and reference it by name. If it needs a new channel or action
representation, extend `evoab/direct/actions.py` (representations) or add the
channel to `session.py`; every agent must be able to drive it.

## Reproduction cards

Keep cards factual and comparable:

| benchmark | protocol | model | success | tokens/episode | cost/episode | runs |
|---|---|---|---|---|---|---|

State the protocol (paper setting versus official leaderboard rules), the
number of runs, and every known difference from the original implementation.
