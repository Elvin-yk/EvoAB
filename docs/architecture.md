# Architecture

How EvoAB is built, and where each kind of extension goes. Development rules for
contributors are in [AGENTS.md](../AGENTS.md); running EvoAB is covered in the
[guide](guide.md).

## Idea

Robot-use agents keep a frozen general-purpose model and differ in the harness
around it. EvoAB makes the harness out of shared components and expresses every
method as a **recipe** over them:

| Axis | Choices | Examples |
|---|---|---|
| What the model sees | Multi-view RGB, proprioception, calibration; optional depth and perception | RUA RGB-only, SAM3 localization |
| How it acts | Tool calls, code, or direct action chunks; end-effector targets with host-side IK, or joints | RUA (tools), PyRUA-Lean (code), Astranomy Direct (chunks) |
| Action models | A VLA/WAM as one callable tool, or none | `agent_policy` |
| Roles | One agent, or roles that delegate to each other | `planner_direct`, Agent team |
| Experience | Skills or programs carried across episodes | `code_iterate` |
| Context | Full history, sliding window, recent observations | per role |

If a paper does not fit, the missing component is added to EvoAB rather than
special-cased in the method.

## Layers

| Layer | Responsibility | Code (`evoab/`) |
|---|---|---|
| Entry | CLI, Studio, Python SDK; same operations everywhere | `cli.py`, `workbench/`, `ui/`, `__init__.py` (SDK exports) |
| Method | Recipes, method library, compiler, graphs, experience | `composition/`, `methods/` |
| Agent | The agent loop, model presets and wire conversion, prompts and context | `agents/`, `provider/`, `context/` |
| Execution | Scheduling, resource exclusion, background jobs, code bridge | `execution/` |
| Tools | Model-visible schemas, handlers and feedback | `tools/` |
| Embodiment | Environment and robot adapters, services, deployment | `integrations/`, `services/` |
| Support | Observations and perception, events, run records, telemetry | `observation/`, `events/`, `artifacts/`, `telemetry/` |

`session.py` assembles one run: it compiles the recipe, prepares the episode,
builds each role's tools and prompt, runs the entry role and finalizes.
`resources/` holds the prompts and skills the robot agent reads at run time;
editing them changes method behavior.

## One run

1. Configuration loads the method, applies `--set` overrides and compiles every
   role against the integration's tool modules (`composition/compiler.py`).
   Errors stop the run here, before any service starts.
2. The environment is started (`--deploy local`) or connected (`--env-url`), and
   its description supplies arms, cameras, calibration, limits and instruction.
3. All roles share one executor; each role sees its own tools through a view.
   `delegate_<role>` tools run another role against the same episode.
4. The entry role runs on its channel: an agent loop for `tools`, programs
   through the code bridge for `code`, the Direct runner for `action`.
5. The run drains outstanding work, finalizes the episode, records the official
   outcome (never shown to the agent) and writes the run directory.

## Components

`evoab methods components --integration X` prints the catalog that recipes are
checked against:

- **Channels**: `tools`, `code`, `action` ([guide](guide.md#methods-and-recipes)).
- **Capabilities** (tool modules published by integrations):

  | Module | Tools |
  |---|---|
  | `gripper` | `move`, `grasp`, `release`, `hold` |
  | `scene` | `view_env_state`, `capture_scene`, `locate` |
  | `skills` | `list_skills`, `read_skill`, optional `write_skill` |
  | `episode` | `finish` |
  | `hybrid` | `actor_act` (VLA actor with optional reviewer) |
  | `delegate` | `delegate_<role>` (harness) |
  | `observation` | observation sidecar profile (harness) |

- **Action representations** (action channel): `abs_eef`, `delta_eef` (through
  the environment's IK), `joint_abs`.
- **Context policies**: `identity`, `sliding_window`, `observation_window`;
  stateless frame windows for action roles.
- **Loops**: single episode, or an episode sequence that carries skills or programs.

Perception: `locate` uses SAM3 when a SAM3 service is configured, and depth or
calibrated multi-view triangulation for 3D. The Observation ToolPackage
(`observation/toolpackage/`, `--obs-pipeline` or `evoab ob-proxy`) runs SAM3,
FoundationPose and mask-IoU NMS as a proxy in front of the environment and adds
annotated images and detections without changing image size; making it a recipe
capability is tracked in [#12](https://github.com/Elvin-yk/EvoAB/issues/12).

## Episodes and the environment protocol

Environments are HTTP services (`services/environment/`, protocol v4). The
environment owns native state, immutable snapshots, one primitive step at a time
and execution receipts. Tools compose those steps into higher-level actions and
check stopping conditions; there are no server-side `move` or `grasp` routes.

- **Snapshots** bind images, calibration and robot state; `locate` reads a
  snapshot, so it can run while the arm moves.
- **Receipts** settle every command. A lost acknowledgement is reconciled, never
  replayed. Model-request retries never authorize physical retries.
- **Metadata** in the environment description adjusts shared tools without new
  schemas: `finish_statuses` (default `["success"]`; also `failure`, `give_up`),
  `motion_profiles`, `physical_hardware`, `motion_execution` (`stepwise` or
  `wholepath`), `normalized_opening_tolerance`.
- **Outcomes**: tool success, agent completion and task success are separate.
  `finish` returns an acknowledgement; the official outcome is recorded for
  evaluation only.

## Scheduling

Each tool declares a policy: return mode (`immediate` or `background`) and
placement (`exclusive` on a resource, or `parallel`). Physical actions,
`capture_scene` and `finish` share the exclusive `episode` resource and run in
submission order; background calls return job handles (`wait_for_tool`,
`get_tool_result`, `cancel_tool`, `flush_queue`). Recipe settings:

| Setting | Values |
|---|---|
| `execution.return_mode` | `immediate`, `background` |
| `execution.resource_scheduling` | `per-resource`, `global-serial` |
| `execution.mode` | `parallel`, or `replanning`: after a failed world-changing action, queued actions are cancelled and new ones are refused until the result is read, the stop is confirmed and a fresh `capture_scene` is taken |

Tools report what an action did in `ToolOutput.action` (`executed` none/some/unknown,
`stopped`, `operator_required`); the executor never reads tool-specific fields.

## Extend EvoAB

| To add | Do this |
|---|---|
| **A method** | A directory in `evoab/methods/` ([guide](guide.md#methods-and-recipes)). Never branch on a method id in code. |
| **A capability** | A `ToolModule` (provider factory `create(session, selection)`, policies, selectable `tools`, typed `options`, label) whose schemas and handlers live in `tools/<family>/`; integrations that support it publish it in `tool_modules`. |
| **A channel or action representation** | Representations in `direct/actions.py`; channels in `session.py`, and every agent must be able to drive it. |
| **An environment** | An integration package with `get_integration_spec()`: native execution, observations and outcome in the adapter, an accurate description (arms, frames, units, limits, cameras, calibration), the tool modules it supports, a deployment recipe, and a section in [benchmarks](benchmarks.md). It must not define its own model tools. |
| **A real robot** | As an environment, plus the device process that owns the hardware, physical limits, interruption and operator commands; follow ARX ONE as the reference. The shared IK contract is tracked in [#18](https://github.com/Elvin-yk/EvoAB/issues/18) and [#22](https://github.com/Elvin-yk/EvoAB/issues/22). |
| **An action model** | An `ActorBackend` registered in `services/actor/registry.py`; environment-specific conversion stays in the integration's binding. |
| **A model vendor or wire format** | A provider adapter in `provider/`; presets reach it through `evoab models`. |
| **An agent runtime** | `agents/<name>/` registered in `agents/registry.py`, using the shared executor and events. Set `runs_programs = True` if it runs code-channel programs in its own terminal. |

## Validation

```bash
python scripts/validation/recipe_composition.py     # recipes, roles, experience, run_program
python scripts/validation/tool_composition.py       # tool schemas, visibility, prompts per configuration
python scripts/validation/observation_toolpackage.py
evoab methods validate METHOD --integration X
```

For a behavior-preserving change to shared tools or prompts, compare
`tool_composition.py --compare SNAPSHOT` against a snapshot taken before the change.
CPU checks are not evidence of simulator or hardware success; state hardware
validation separately.

## Packaging

Two distributions share one version: `evoab` (CLI, SDK, Studio) and
`evoab-runtime` (installed inside native and container runtimes). Build both with
`python scripts/release/prepare.py --output-dir DIR` and check them with
`scripts/validation/release_contents.py` and `scripts/validation/sdk_release.py`;
`evoab setup --wheel` accepts only the matching runtime wheel.
