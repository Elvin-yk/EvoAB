# Guide

Install EvoAB, connect models, and run, compose and inspect methods. How it is
built and extended is in [architecture](architecture.md); preparing each
simulator or robot is in [benchmarks](benchmarks.md).

## Install

Linux with Python 3.11 (3.12 works for the CLI) and Docker: every environment
and model service runs in a container. macOS can view runs and drive remote
environments.

```bash
git clone https://github.com/Elvin-yk/EvoAB.git ~/evoab && cd ~/evoab
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[agent]'
evoab doctor
```

If cloning fails, run `gh auth login` and `gh repo clone Elvin-yk/EvoAB ~/evoab`.
Extras add harness-side dependencies only (`agent` is required; `robodojo`,
`benchmarks`, `xpolicylab`, `arx_one`, `piper_x`, `dev`); simulator and model
images, assets and checkpoints are prepared separately ([benchmarks](benchmarks.md)).

**Docker**: Docker Engine and the user in the `docker` group. GPU benchmarks and
model services also need the NVIDIA Container Toolkit (`sudo nvidia-ctk runtime
configure --runtime=docker`, then restart Docker); MetaWorld runs on the CPU.
Allow about 120 GiB of disk for the GPU benchmarks.
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
**发起任务** picks benchmark, task (RoboDojo: by capability dimension, with seed
and layout), method and model; **运行记录** replays runs step by step with token
cost; **设置** manages models and benchmark runtimes. Every Studio action is also
a command (`evoab runs launch|list|trace|stop`, `evoab benchmarks list|tasks`,
add `--json`); the HTTP API is at `/openapi.json`. Studio is frozen while new
capabilities land in the CLI, recipes and SDK first.

## Run from the command line

One `evoab run` is one episode. The split is strict:

- **The recipe says how the agent works**: what it is given (`inputs`), which
  channel it acts through, which tools and options it has, its context, budget
  and loop. Change it in the YAML or for one run with `--set PATH=VALUE`.
- **The command line says which run**: integration, task and scene, where the
  environment and services run, which model, and where output goes.

EvoAB starts the benchmark's container for the episode and stops it afterwards;
you never start environments or pass their addresses yourself.

```bash
evoab run --integration robodojo --task stack_bowls \
  --recipe agentic --model my-model --output-dir ./runs --monitor

# the same method, RGB only and with the environment's task supplements
evoab run --integration robodojo --task stack_bowls --recipe agentic --model my-model \
  --set inputs.depth=false --set inputs.task_supplements=true
```

| Flag | Selects |
|---|---|
| `--integration`, `--recipe`, `--set` | The environment adapter, the method (id, directory or file), recipe overrides |
| `--task`, `--seed`, `--layout-id`, `--layout`, `--instruction` | The episode and scene |
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

A method is a directory in `evoab/methods/` with `method.yaml` (the recipe) and
`CARD.md` (paper, setting, results). A recipe has **inputs**, one or more
**roles** and the **loop** connecting them.

```yaml
id: planner_direct
label: Planner + Direct controller
inputs: {depth: false}                 # optional; null keeps the environment's default
roles:
  planner:
    channel: tools
    capabilities:
      - skills
      - {id: scene, tools: [view_env_state, capture_scene], options: {segmentation: false}}
      - episode
      - {id: delegate, options: {roles: [controller]}}
    prompt: {append: "Split the task into short, verifiable sub-goals …"}
    max_turns: 0
  controller:
    channel: action
    action: {representation: abs_eef, horizon: [3, 15], history: [15, 7], max_calls: 6}
loop: {entry: planner}
execution: {return_mode: immediate}
```

| Part | Settings |
|---|---|
| `inputs` | `depth`, `calibration` (applied when EvoAB starts the environment), `task_supplements` (environments that publish them) |
| Role `channel` | `tools` (tool calls), `code` (Python programs against the robot API, run through the `run_program` tool), `action` (one stateless request per action chunk: `abs_eef`, `delta_eef`, `joint_abs`) |
| Role `capabilities` | Tool modules, optionally narrowed (`tools`) and configured (`options`); `{id: X, optional: true}` is skipped where missing. `scene` has `segmentation` (SAM3 for `locate`); `hybrid`, the policy tool, has `binding` and `review` |
| Role `prompt`, `max_turns`, `brain` | Method instructions, turn budget, an optional model preset or effort for this role |
| `loop` | Entry role; `episodes.repeat`, `carry: [skills]` or `[programs]`, `keep: success\|always` |
| `execution` | Return mode, resource scheduling, replanning ([architecture](architecture.md#scheduling)) |

Every built-in `method.yaml` writes out its settings with a comment on each, so
reading the file is the documentation. Change a value in a copy of the file, or
for one run with `--set` and the value's dotted path; capabilities are addressed
by id and values are read as YAML:

```bash
--set 'roles.main.action.horizon=[16,32]'
--set 'roles.main.capabilities.gripper.tools=[move,hold]'
--set inputs.depth=false
```

**Your own tools.** A Python function marked `@tool` is an agent tool: the
docstring is the description the model sees, type hints are the arguments, and
the first parameter, `robot`, calls the episode's other tools. Select the file
with the `user_tools` capability; it works with any environment and channel.

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
with its own brain, channel and tools against the same episode and returns a
report. Validation is offline and strict: unknown fields, unreachable roles and
missing capabilities fail before anything starts.

```bash
evoab methods list --integration robodojo       # ids and where each method.yaml lives
evoab methods components --integration robodojo
evoab methods validate ./my_method/ --integration robodojo
```

**Add a method**: copy the closest directory in `evoab/methods/`, edit
`method.yaml`, put instructions in `prompt.append` or `prompts/*.md`, write
`CARD.md` (paper and code links, setting, what is and is not reproduced, results),
validate against every target integration, run one episode and check
`recipe.json` and `effective_prompt.md`. Run it with `--recipe ./my_method/` or add
a root with `EVOAB_METHODS_DIR`. If it needs something missing, add a component
([architecture](architecture.md#extend-evoab)), never special-case the method.

## Results

Each run writes a directory under `--output-dir`: `result.json` (configuration,
recipe, outcome), `recipe.json`, `effective_prompt.md` and `tools.json` (exactly
what the agent saw), `trajectory.jsonl`, content-addressed `images/` and
`artifacts/`, `roles/NNN_<role>/` for delegations and `direct/` for action-chunk
requests. `evoab replay ./runs` replays saved runs in the browser without a model.

## Python SDK

```python
from evoab import AgentConfig, SessionConfig, run

result = run(SessionConfig(
    integration="robodojo", env_url="http://127.0.0.1:18760",
    recipe="planner_direct", recipe_overrides={"roles.controller.action.max_calls": 4, "inputs.depth": False},
    agent=AgentConfig(model="my-model"),
))
print(result.task_success)
```

`Session(SessionConfig(...))` gives direct access to the episode's tools;
`setup()` and `deploy()` prepare and start service containers. Examples are in
[`examples/sdk/`](../examples/sdk/).
