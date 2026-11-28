# Development instructions

These instructions apply to agents developing this repository. They are not
robot-task instructions, runtime skills, or permission to operate hardware.
When running a robot episode, use its assembled prompt and registered tools;
do not treat this document as a request to edit code during the episode.

## Before changing code

- Check the checkout, branch, status, and relevant diff. Preserve other work;
  stage only task-owned changes when a commit is requested.
- Read [architecture](docs/architecture.md) for the affected layer and where
  each kind of extension belongs. For environment or robot work, also read
  [benchmarks](docs/benchmarks.md).
- Identify the owner of each new behavior before implementing it. Existing
  code is evidence, not permission to copy a boundary violation.

## Architecture and extension points

All components can evolve, including existing gripper tools. Keep each behavior
in its owning layer and make cross-layer contracts explicit. The current tool
families are examples of this architecture, not a closed list of allowed tools.

| Layer | Responsibility |
|---|---|
| `cli.py`, `config.py`, `session.py` | Resolve configuration and assemble/own the run lifecycle |
| `recipe/`, `methods/` | Recipes (roles, `act`, capabilities, loops), their compilation, the method library (tools, code, action) and graphs |
| `agent/` | The built-in agent: `loop.py` runs the model over a role's calls; `act/` the three ways of acting (`code/`: run_program and bridge, `action/`: chunk representations, the action tool and the stateless loop); `context/` prompts; `models/` presets; `memory/` experience; `team/` roles and delegation |
| `tools/` | Define model-visible tool schemas, handlers, orchestration, feedback and resource requirements |
| `runtime/` | Schedule tool work, enforce resource exclusion and manage background jobs |
| `envs/` | Adapt native environments (one package per benchmark) and compose tool families; `protocol/` the environment service contract and client, `observation/` observation collection, `docker/` container setup and start |
| `servers/` | User-run actor, perception and session verification services, including their contracts, clients and hosts |
| `records/` | Run artifacts, telemetry and shared events |
| `apps/` | Console, Studio, replay and workbench commands: present state and route user intent through session interfaces |
| `resources/` | Package robot-facing prompts and runtime skills |

- To add or reproduce a method, write a recipe over the three ways of acting and
  the capabilities; follow [the add-method skill](docs/agent_skills/add-evoab-method/SKILL.md).
  `evoab/methods/` holds only `tools`, `code` and `action`. Never branch on a
  method id in code; extend recipe contracts instead.
- To change robot behavior, modify or add tools in an existing tool set, or
  create `tools/<family>/` for a new tool set. Keep reusable behavior there even
  when the first user is one environment. Avoid concrete integration imports;
  accept domain contracts and bindings instead.
- Environments provide native execution/data and compose shared providers;
  they do not define model tools or hide tool-name dispatch inside bindings.
  Reuse existing episode/service infrastructure and extend its contracts when
  needed. Services expose domain operations, not a copy of the model tool API.
- Reuse compatible observation contracts and scene tools. Extend them or add
  a tool family for a different agent capability; native sensor setup belongs
  in the adapter. Expose only capabilities the selected environment supports.
- Keep the agent loop in `agent/`, model connections in `agent/models/` presets,
  and scheduling in `runtime/`. Rendering and logging consume
  shared state/events; they do not implement robot control.
- Keep native dependencies at service/startup boundaries so CLI help and offline
  configuration validation do not require robot or model runtimes.

## Compatibility and validation

- The root `tests/` directory is local-only and ignored. Keep validation files
  there untracked; never use `git add -f` or remove the ignore rule to commit them.

- Intentional behavior changes are welcome: document the new contract, affected
  users and any migration, then update tests/docs. For a behavior-preserving
  refactor, compare tool schemas/visibility, prompts, feedback, defaults, budgets,
  ordering and outcomes. Neither existing tools nor public APIs are frozen.
- Check affected callers and unsupported-capability cases when changing shared
  contracts. Scale regression coverage to the change rather than requiring a
  full benchmark for every contribution.
- Preserve command identity, state ownership and receipt reconciliation. A lost
  physical acknowledgement does not justify replaying a motion. Distinguish
  tool completion, agent completion and environment task success.
- Use English for code comments, docstrings, model-facing text, and repository
  documentation. Prefer explicit types and small functions; avoid speculative
  compatibility layers. Set up with the [guide](docs/guide.md#install).
- Run relevant validation and report failures/skips honestly. Follow
  [validation](docs/architecture.md#validation) and add focused
  tests for new behavior. CPU checks are not evidence of real-hardware success.
- Do not put credentials, machine-specific deployment paths, or experiment
  outputs in source. Preserve upstream attribution and native resource formats.

## Code Review Rules

Flag integration-local model tools, generic `execute_tool(name, args)` service
routes, a fourth way of acting, tool-family imports of native integrations, duplicated episode state,
unsupported capabilities advertised to the planner, agent-specific decisions in
generic scheduling, and robot control implemented in UI/telemetry. Review changes
to shared contracts for affected callers and migration, not for conformity to
an immutable tool list. For migrations, account for retained, replaced and
deliberately removed features.

Maintain shared rules here. `CLAUDE.md` only imports this file. Human
documentation is four files: `README.md`, `docs/guide.md` (using EvoAB),
`docs/architecture.md` (how it is built and extended) and `docs/benchmarks.md`
(preparing environments). Update the one that owns a changed contract; do not
add new documentation files.
