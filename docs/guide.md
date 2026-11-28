# Guide

Install EvoAB, connect models, and run, compose and inspect methods. How it is
built and extended is in [architecture](architecture.md); preparing each
simulator is in [benchmarks](benchmarks.md).

## Install

Use Linux with Python 3.11 (3.12 works for the CLI) and Docker to run a benchmark.
Methods that use a policy or SAM3 connect to a service you run separately. macOS can view runs and drive remote
environments.

```bash
git clone https://github.com/Elvin-yk/EvoAB.git ~/evoab && cd ~/evoab
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[agent]'
evoab doctor
```

If cloning fails, run `gh auth login` and `gh repo clone Elvin-yk/EvoAB ~/evoab`.
Extras add harness-side dependencies only (`agent` is required; `benchmarks`,
`dev`); benchmark images and model checkpoints are prepared separately
([benchmarks](benchmarks.md)).

**Docker**: Docker Engine and the user in the `docker` group, plus the NVIDIA
Container Toolkit (`sudo nvidia-ctk runtime configure --runtime=docker`, then
restart Docker). Allow about 60 GiB of disk for the benchmark images.
`evoab benchmarks check` reports anything missing.

**Networks.** If proxy variables break HTTPS to PyPI, install without them
(`env -u https_proxy -u http_proxy …`); in mainland China add
`--index-url https://pypi.tuna.tsinghua.edu.cn/simple`. Start Studio with
`env -u all_proxy -u ALL_PROXY` when `all_proxy` is a SOCKS proxy.

**Coding agents installing EvoAB** check `nvidia-smi` and free disk, install as
above, set up Docker GPU access asking before every `sudo`, start Studio in tmux,
run `evoab benchmarks check` and hand over the Studio URL. They never ask for,
type or store API keys: the user enters them in Studio.

## Models

The built-in agent calls models through **presets**: a provider (base URL,
protocol, key) plus a model ID with its limits and reasoning effort. Presets and
recorded usage live in `~/.config/evoab/agent/` (keys mode 600), managed in
Studio (设置 → 模型) or with the CLI, which reads keys only from an environment
variable or standard input:

```bash
evoab models add-provider "My API" --base-url https://host/v1 --key-env MY_API_KEY
evoab models add-preset my-model --provider my_api --model MODEL_ID --effort medium
evoab models list
evoab usage
```

Every method runs on EvoAB's built-in agent; `--model` picks the preset.

## Studio

```bash
tmux new-session -d -s evoab-studio "evoab studio --runs-root ~/evoab-runs --port 8770"
```

Open `http://<host>:8770`. The header shows the commit Studio runs and turns
amber when that build has local changes or was updated while the page was open.
**发起任务** picks benchmark, task, method and model; **运行记录** replays runs step by step with token
cost; **设置** manages models and benchmark runtimes. Every Studio action is also
a command (`evoab runs launch|list|trace|stop`, `evoab benchmarks list|tasks`,
add `--json`); the HTTP API is at `/openapi.json`. Studio is frozen while new
capabilities land in the CLI, recipes and SDK first.

In **发起任务**, choose **仿真 → OpenArm Gazebo**, then select `reach_right` or
`reach_left`. Studio starts with one Actor. Its editable **任务描述** is prefilled
with the selected wrist task and becomes the episode's user message. For a team,
change the first role to Planner and add an Actor.
The Planner receives the task description and delegates a subtask to the Actor.
Choose a model preset for each role or let a delegated role follow the entry model.
A blank description uses the benchmark instruction. Other built-in simulations use
the task selected with `--task` in Studio, the CLI and the SDK. Their role prompts
give role-specific guidance. The confirmation screen shows the actual instruction.
OpenArm scores the benchmark's fixed 40 mm upward target. Inspect the measured
pose in the tool results when requesting other motions.

## Run from the command line

One `evoab run` is one episode. The split is strict:

- **The recipe says how the agent works**: what it is given (`inputs`), how it
  acts (`act`: tools, code, action), which tools and options it has, its context, budget
  and loop. Change it in the YAML or for one run with `--set PATH=VALUE`.
- **The command line says which run**: integration, task and scene, where the
  environment and services run, which model, and where output goes.

EvoAB starts the benchmark's container for the episode and stops it afterwards;
you never start environments or pass their addresses yourself.

```bash
evoab run --integration calvin --task open_drawer \
  --recipe tools --model my-model --output-dir ./runs --monitor

# the same method, RGB only, acting through tools and action chunks
evoab run --integration calvin --task open_drawer --recipe tools --model my-model \
  --set inputs.depth=false --set 'roles.main.act=[tools,action]'
```

| Flag | Selects |
|---|---|
| `--integration`, `--recipe`, `--set` | The environment adapter, the method (id, directory or file), recipe overrides |
| `--task`, `--runtime-profile`, `--seed` | The episode (LIBERO: the suite is the runtime profile) |
| `--instruction` | Operator-supplied episode instruction for OpenArm Gazebo |
| `--env-gpu` | Which GPU the environment's container uses |
| `--sam3-url`, `--action-model-url`, `--action-model-checkpoint-sha256` | Where perception and policy services run, and which checkpoint |
| `--model`, `--reasoning-effort` | Which model preset, and its effort |
| `--episode-timeout-s`, `--output-dir`, `--experience-dir`, `--monitor` | Wall-clock budget, outputs, live console |
| `--debug` | Verbose logs and raw model requests in the run record; never changes what the agent sees or does |
| `--validate-config`, `--print-config` | Check or print the resolved configuration offline |

`evoab run --integration X --help` lists everything.

**Outcomes.** Tool success, agent completion and task success are separate. The
agent ends with `finish`, which returns an acknowledgement; the environment
decides success and the agent never sees the verdict.

## Methods and recipes

An agent acts in three ways: it calls **tools**, writes **code** (programs that
call the same tools through `run_program`), or emits **action** chunks. A role's
`act` enables any combination. The built-in methods `tools`, `code` and `action`
(`evoab/methods/<id>/`, each a commented `method.yaml` plus `CARD.md`) are the
three alone; everything else is a recipe over them, such as those in
[`examples/recipes/`](../examples/recipes/).

| `act` | The agent session |
|---|---|
| `[tools]` | Calls tools and reads each result |
| `[code]` | Runs programs with `run_program`; only skill reading stays a direct call |
| `[action]` | No session: each inference is one stateless request that must return an action chunk (Astranomy's Direct form) |
| `[tools, code]` | Calls tools directly or runs programs that call them |
| `[tools, action]`, `[code, action]`, `[tools, code, action]` | The chunk tool (`emit_eef_actions` and similar) is one more call; its result has the measured state and current images |

A recipe has **inputs**, one or more **roles** and the **loop** connecting them.

```yaml
id: tools_and_policy
label: Tools and a VLA policy
inputs: {depth: false}                 # optional; null keeps the environment's default
roles:
  main:
    act: [tools]
    capabilities:
      - skills
      - {id: scene, tools: [view_env_state, capture_scene], options: {segmentation: false}}
      - {id: hybrid, tools: [actor_act], options: {review: false}}
      - episode
    max_turns: 0
loop: {entry: main}
execution: {return_mode: immediate}
```

| Part | Settings |
|---|---|
| `inputs` | `depth` (applied when EvoAB starts the environment) |
| Role `act` | Any of `tools`, `code`, `action`; `action` settings (`representation`: `abs_eef`, `delta_eef`, `joint_abs`; `horizon`, `step_limits`, and `history` when acting only through action) |
| Role `capabilities` | Tool modules, optionally narrowed (`tools`) and configured (`options`); `{id: X, optional: true}` is skipped where missing. `scene` has `segmentation` (SAM3 for `locate`); `hybrid`, the policy tool, has `binding` and `review` |
| Role `prompt`, `max_turns`, `brain` | Method instructions, turn budget, an optional model preset or effort for this role |
| `loop` | Entry role; `episodes.repeat`, `carry: [skills]` or `[programs]`, `keep: success\|always` |
| `execution` | Return mode, resource scheduling, replanning ([architecture](architecture.md#scheduling)) |

Every built-in `method.yaml` writes out its settings with a comment on each, so
reading the file is the documentation. Change a value in a copy of the file, or
for one run with `--set` and the value's dotted path; capabilities are addressed
by id and values are read as YAML:

```bash
--set 'roles.main.act=[tools,action]'
--set 'roles.main.action.horizon=[16,32]'
--set 'roles.main.capabilities.gripper.tools=[move,hold]'
--set inputs.depth=false
```

Each arm target in the shared gripper `move` accepts either an absolute
`pose=[x,y,z,roll,pitch,yaw]` or a relative `translation_m=[dx,dy,dz]`.
A translation uses the control-frame axes and retains the wrist orientation
measured when the command starts. Submitting a second translation requests
another displacement.

**Your own tools.** A Python function marked `@tool` is an agent tool: the
docstring is the description the model sees, type hints are the arguments, and
the first parameter, `robot`, calls the episode's other tools. Select the file
with the `user_tools` capability; it works with any environment and way of acting.

```python
from evoab import tool
from evoab.tools.gripper.pose import quaternion_to_rpy

@tool
def lift_by(robot, arm: str, height_m: float = 0.05) -> str:
    """Raise one arm's grasp site straight up by height_m metres, keeping its orientation."""
    pose = robot.view_env_state()["state"]["arms"][arm]["control_pose"]   # position + wxyz quaternion
    x, y, z = pose["position"]
    robot.move(targets=[{"arm": arm, "pose": [x, y, z + height_m, *quaternion_to_rpy(pose["quaternion"])]}])
    return "lifted"
```

```yaml
capabilities: [skills, scene, gripper, episode, {id: user_tools, options: {file: my_tools.py}}]
```

A tool returns text, a dict, a PIL image or `(text, [images])`; a failing inner
call reaches the model as this tool's error. See `examples/tools/lift.py`.

`delegate` exposes other roles as `delegate_<role>(instruction)`: the role runs
with its own brain, ways of acting and tools against the same episode and returns a
report. Validation is offline and strict: unknown fields, unreachable roles and
missing capabilities fail before anything starts.

```bash
evoab methods list --integration calvin         # ids and where each method.yaml lives
evoab methods components --integration calvin
evoab methods validate ./my_method.yaml --integration calvin
```

**Add a method**: copy the closest `method.yaml` or example recipe, choose `act`
and capabilities, put instructions in `prompt.append`, record the paper in
`references`, validate against every target integration, run one episode and
check `recipe.json` and `effective_prompt.md`. Run it with `--recipe ./my_method.yaml`
or add a root with `EVOAB_METHODS_DIR`. If it needs something missing, add a component
([architecture](architecture.md#extend-evoab)), never special-case the method.

## Results

Each run writes a directory under `--output-dir`: `result.json` (configuration,
recipe, outcome), `recipe.json`, `effective_prompt.md` and `tools.json` (exactly
what the agent saw), `trajectory.jsonl`, content-addressed `images/` and
`artifacts/`, `roles/NNN_<role>/` for delegations and `direct/` for action-chunk
requests. `evoab replay ./runs` replays saved runs in the browser without a model.

To exercise the verifier transport, start the stub in a separate terminal with
a shared directory containing the session files and environment image archive.

```bash
python -m evoab.servers.verifier.server --runs-root ./outputs --port 18780
```

Add `--verifier-url http://127.0.0.1:18780` to `evoab run`, or set
`SessionConfig.verifier_url` in the Python SDK. This option uses the EvoAB
Session lifecycle. Use the same absolute filesystem paths on the service hosts.
Restart the benchmark container after updating the mounted EvoAB checkout so
its environment service publishes recorded image paths. For a wheel-based
runtime, rebuild with the updated wheel. Each `observation_sampled` row in `trajectory.jsonl` identifies its
native step, capture time, snapshot manifest and per-camera PNG paths.

The stub exercises queueing, file access, atomic persistence and completion
acknowledgement. Its `verification.backend` is `stub` and its placeholder
verdict is `insufficient_evidence`. The native evaluator supplies the task
score. `--verifier-timeout-s` defaults to 30 seconds. When completion cannot
be confirmed, the run reports a nonzero exit code. Inspect `result.json` for
the latest persisted verification status. A queued job can finish after the
request times out.

## Python SDK

```python
from evoab import AgentConfig, SessionConfig, run

result = run(SessionConfig(
    integration="calvin", env_url="http://127.0.0.1:18760",
    recipe="code", recipe_overrides={"roles.main.act": ["tools", "code"]},
    agent=AgentConfig(model="my-model"),
))
print(result.task_success)
```

`Session(SessionConfig(...))` gives direct access to the episode's tools;
`setup()` and `deploy()` prepare and start benchmark containers. Examples are in
[`examples/sdk/`](../examples/sdk/).
