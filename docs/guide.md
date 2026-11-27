# Guide

Install EvoAB, connect models, and run, compose and inspect methods. How it is
built and extended is in [architecture](architecture.md); preparing each
simulator or robot is in [benchmarks](benchmarks.md).

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
Extras add harness-side dependencies only (`agent` is required; `robodojo`,
`benchmarks`, `xpolicylab`, `arx_one`, `piper_x`, `dev`); benchmark images, assets
and model checkpoints are prepared separately ([benchmarks](benchmarks.md)).

**Docker**: Docker Engine and the user in the `docker` group. GPU benchmarks
also need the NVIDIA Container Toolkit (`sudo nvidia-ctk runtime
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

## Compose APIs in a recipe

Name each interface under `apis`. `kind: model` uses a model preset and the built-in
agent. Its `calls` list exposes named APIs to that model and `capabilities` selects
environment tools. Each model can have its own `brain.model`, `input_schema`,
`output_schema`, and tool list. A caller sees another model as
`delegate_<name>`; the harness validates the arguments and returns its validated
JSON result in `value`. A child starts with the explicit arguments, its own prompt
and any selected environment context. A named model that only calls other APIs
receives the handoff without the robot prompt. Set `inputs.environment` to choose
explicitly. Its transcript stays in its own run directory.
The parent receives its report. The same model preset can serve several independent
interfaces. `execution.max_call_depth` bounds nested calls (default 8).

A single model or flow is the default entry. With multiple controllers set
`loop.entry`. The harness starts the environment first. `loop.arguments` supplies
additional JSON input fields to the entry (its initial input includes `instruction`).
Existing methods under `roles` run through the same compiler.

- **Model input.** Global `prompts.start` and `prompts.each_call` are ordered lists
  of text file paths. Each model can add its own paths with the same fields. Paths
  resolve relative to the recipe. Start text enters the first request of each
  invocation. In a conversational model it then stays in that invocation's history.
  Each-call text is part of the system instructions. Direct builds an independent
  request each time. The method can also append inline text with `prompt.append`.
- **Direct output.** Use `channel: action`, with `inputs.history` and `inputs.blocks`
  for observation history and prompt content, `output.representation` and
  `output.horizon` for the emitted actions, and `budget.max_calls` and
  `budget.repair_attempts` for inference. The top-level environment `inputs` cover
  depth, calibration, and optional task supplements. See [the Direct API recipe](../examples/recipes/direct_api.yaml).
- **External services.** `kind: http` calls a user-operated JSON service with
  `url`, `input_schema` and `output_schema`. POST sends a JSON object, GET sends
  query parameters. For bearer authentication set `bearer_env` to the environment
  variable name. Use the actor protocol for policies that drive the robot.
- **Policies.** Give each `kind: policy` its own actor URL, expected checkpoint
  SHA-256, and environment binding. The policy server owns loading the model and
  weights. The harness checks its identity and uses the integration's existing
  observation and action binding. Each selected policy owns its own client and
  shares the episode's exclusive motion resource. `review` and `review_model`
  can be set per policy.
- **Explicit wiring.** A `kind: flow` executes ordered `steps`. Each step names a
  callable and builds its argument object from literals and `{from: step.field}`
  references. A `call` with `source` and `choices` routes a string returned by
  an earlier step to one of the declared APIs. A failed request, missing field,
  invalid response or unknown choice stops the flow at that step. Its result
  is the last successful step's value on normal completion. A flow with only
  HTTP services and tools can run without an LLM preset.

[The routing example](../examples/recipes/routed_policy.yaml) wires a scene
service to a model that can consult a second model, then calls the chosen CALVIN
policy. Replace its example URLs and checkpoint digests with the identities
of your running services. Validate offline with
`evoab methods validate ./examples/recipes/routed_policy.yaml --integration calvin`.
Use this path as `--recipe` in `evoab run`. Inspect `flow.jsonl` for the route,
`roles/*/handoff.json` for parent invocation and transferred input, and each
invocation's model records for its actual requests.

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
`setup()` and `deploy()` prepare and start benchmark containers. Examples are in
[`examples/sdk/`](../examples/sdk/).
