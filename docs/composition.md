# Composition: recipes, roles and channels

Every EvoAB run executes a **method**, and every method is a **recipe**: a YAML
(or JSON) document that names one or more roles, the loop connecting them and
the execution settings their tools share. The recipe is the single source of
truth; the CLI, the Python SDK and Studio all compile the same recipe
(`evoab/composition/compiler.py`) before any environment or model starts.

## Three axes instead of one mode

Earlier versions selected one *interaction mode* (tool calling, code as policy
or direct policy) and one *tool profile*. A recipe separates what those mixed:

| Axis | Values | Meaning |
|---|---|---|
| **channel** | `tools`, `code`, `action` | How a role reaches the robot: native tool calls; programs against a generated robot API; one forced action-chunk call per stateless inference |
| **capabilities** | `skills`, `scene`, `gripper`, `hybrid`, `episode`, `delegate`, `observation` | Which tool modules the role may use, optionally narrowed to some tools |
| **context** | `identity`, `sliding_window`, `observation_window` (stateless frames for `action`) | How much history each request carries |

The paradigms of agent-based robot control map onto these axes:

- **Orchestrate**: a `tools` or `code` role whose capabilities include
  perception, motion primitives, skills and, when available, an action model
  (`hybrid`).
- **Direct control**: an `action` role. Representations `abs_eef` and
  `delta_eef` go through the environment's IK; `joint_abs` sends joint targets.
- **Code**: a `code` role; programs call the same registered tools with the
  same validation and scheduling.

Combinations come from several roles: a role can expose others as tools
(`delegate`), and the loop can repeat episodes while carrying experience.

## Recipe format

```yaml
id: custom_team                    # must match a custom method directory name
label: Planner + Direct controller
summary: One line shown in listings and Studio.
paradigms: [orchestrate, direct]   # orchestrate | direct | code | policy
status: experimental               # builtin | reproduction | experimental
integrations: null                 # or a list restricting where the method runs
references:
  - {title: "Paper title", url: "https://arxiv.org/abs/…", kind: paper}
roles:
  planner:
    description: Shown to the caller of delegate_planner and in graphs.
    brain: {model: null, reasoning_effort: null}   # agent: null = the run's agent
    channel: tools
    style: full                    # full | compact (the four-tool baseline style)
    initial_observation: false     # first message carries an RGB observation
    capabilities:
      - skills                     # shorthand for {id: skills}
      - {id: scene, tools: [view_env_state, capture_scene]}
      - {id: skills, options: {write: true}}       # typed module options
      - {id: gripper, optional: true}              # skipped where unavailable
      - {id: delegate, options: {roles: [controller]}}
    context: {policy: identity}
    prompt: {append: "Method instructions…", files: [prompts/planner.md]}
    max_turns: 0
  controller:
    channel: action
    action:
      representation: abs_eef      # abs_eef | delta_eef | joint_abs
      horizon: [3, 15]             # rows per chunk
      history: [15, 7]             # frame window and stride
      step_limits: [0.02, 0.1]     # metres, radians per row
      joint_step_limit_rad: 0.05
      blocks: null                 # prompt blocks; default all
      custom_prompt: ""
      repair_attempts: 2
      max_calls: 6                 # inferences per run of this role; 0 = until the episode ends
loop:
  entry: planner                   # the role that runs the episode
  episodes: {repeat: 1, carry: [], keep: success, stop_on_success: false}
execution:
  return_mode: immediate           # immediate | background
  resource_scheduling: per-resource
  mode: null                       # null = integration default; parallel | replanning
  observation_profile: none
parameters:                        # values launchers may set by name
  - {name: controller_calls, path: roles.controller.action.max_calls,
     label: Controller inferences per sub-goal, type: integer}
```

Validation is offline and strict: unknown fields, a role unreachable from
`loop.entry`, a delegate back to the entry role, an action role with
capabilities, a capability the integration lacks (unless `optional`), a tool a
module does not offer, or a channel the agent cannot drive all fail before any
service starts.

## Overrides

`--set` takes a declared parameter name or any dotted path. Capabilities are
addressed by id:

```bash
evoab run … --recipe direct_policy --set horizon=3:10 --set representation=joint_abs
evoab run … --recipe agentic --set roles.main.context.policy=observation_window \
  --set roles.main.capabilities.gripper.tools='[move, hold]'
evoab run … --recipe agentic --set execution.mode=replanning
```

`--agent`, `--model`, `--reasoning-effort` and `--max-turns` override the entry
role's brain. With `--model-preset`, `--model` names a Studio model preset and an
HTTP agent (Codex, Claude Code) takes its model ID, base URL, wire profile and
API key from model settings; this is how Studio launches code methods. The run directory records the compiled recipe and the overrides in
`recipe.json`; `result.json` records `recipe`, `channel`, `tool_style`,
`tool_modules` and `roles`.

In Python:

```python
from evoab import AgentConfig, SessionConfig, run
run(SessionConfig(integration="robodojo", env_url=URL, no_sam3=True,
                  recipe="agent_policy",
                  recipe_overrides={
                      "roles.main.capabilities.hybrid.options.binding": "robodojo_direct_eef",
                  },
                  agent=AgentConfig(agent_backend="native", model="gpt-6-astra")))
```

## Roles and delegation

The entry role runs the episode. A `delegate` capability exposes each named role
as a tool `delegate_<role>(instruction)`; the session runs that role with its
own brain, channel, context and tool view against the same episode and returns
a report (success, message, turns, tool calls, steps, the role's run directory).

- All roles share one executor; each role sees only its own tools through a
  `ToolView`, so resource exclusion and receipts stay in one place.
- An `action` role steps the environment directly, so its delegation occupies
  the exclusive `episode` resource for the whole sub-run.
- A role without `brain.agent` follows the run's agent, and without `brain.model`
  the run's model; a role pinned to another agent reuses the run's model preset.
- Sub-role prompts are saved as `prompts/<role>.md`; sub-runs write their records
  under `roles/NNN_<role>/`.

## Episodes and experience

`loop.episodes.repeat > 1` makes `evoab run` start that many episodes in
sequence (each an ordinary run with its own reset). `carry` names what moves
forward:

- `programs` (code entry role): `scratch/programs/` starts with the kept
  programs and replaces them after the episode when `keep` allows;
- `skills` (entry role with `skills: {write: true}`): a complete skills library,
  seeded from the packaged one, staged per episode and promoted the same way.

`keep: success` promotes only after an officially successful episode; `always`
promotes after every episode. The experience directory defaults to
`<output>/experience/<method>/<integration>/` (`--experience-dir` to choose) and
`episodes.jsonl` there logs every attempt.

## Components and graphs

`evoab methods components --integration X` lists every building block: brains
(agent runtimes and the channels they drive), channels, action representations,
context policies, harness capabilities and the integration's tool modules.
`evoab methods graph ID` prints a recipe as nodes and edges; Studio annotates it on the run page with executed tool calls and delegations.
A role may set `job_tools` to list the background controls it exposes. A selection without a result-reading control
makes ordinary tool calls wait for completed results. Omitting the field
preserves the executor defaults.

## Methods and agents are independent

A method never requires a particular agent. `brain.agent` is left empty in the
built-in recipes, so the agent chosen for the run (`--agent`, Studio's agent
picker; default `native`) drives every tools or code role, with any model
configured in model settings:

| Channel | How any agent runs it |
|---|---|
| `tools` | The agent's own loop calls the registered tools |
| `code` | Codex and Claude Code run programs in their own terminal; every other agent gets one harness tool, `run_program`, backed by the same bridge, API and finish handling |
| `action` | No agent loop: each inference is one stateless request to the role's model (the direct runner), whichever agent was chosen |

The only exception is an external evaluator such as `rua` (behind
`rua_upstream`), which owns its whole episode loop and runs only the method
written for it.
