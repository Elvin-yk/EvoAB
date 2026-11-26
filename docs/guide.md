# Guide

How to install EvoAB, connect models, and run, compose and inspect methods.
For how it is built and extended, see [architecture](architecture.md); for
preparing each simulator or robot, see [benchmarks](benchmarks.md).

## Install

EvoAB runs on Linux with Python 3.11 (3.12 works for the CLI; some native
runtimes require 3.11). macOS can view runs and drive remote environments.

```bash
git clone https://github.com/Elvin-yk/EvoAB.git ~/evoab && cd ~/evoab
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[agent]'
evoab doctor
```

The repository may be private: if cloning fails, run `gh auth login` and use
`gh repo clone Elvin-yk/EvoAB ~/evoab`. Extras add harness-side dependencies only;
simulators, assets and model checkpoints are prepared separately
([benchmarks](benchmarks.md)).

| Extra | Adds |
|---|---|
| `agent` | The built-in agent (required to run methods) |
| `robodojo`, `robolab`, `benchmarks` | Geometry and media dependencies for those integrations |
| `xpolicylab` | Clients for XPolicyLab actor services |
| `arx_one`, `piper_x` | Real-robot adapters |
| `dev` | Build and release tools |

**GPU benchmarks run in Docker.** Install Docker Engine, add the user to the
`docker` group, install the NVIDIA Container Toolkit
(`sudo nvidia-ctk runtime configure --runtime=docker`, then restart Docker), and
check `docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi`.
Allow about 120 GiB of disk for images and assets. `evoab benchmarks check`
reports anything missing.

**Networks.** If shell proxy variables break HTTPS to PyPI, install without them
(`env -u https_proxy -u http_proxy …`). In mainland China, add
`--index-url https://pypi.tuna.tsinghua.edu.cn/simple`; Studio chooses package
and model mirrors for container builds by itself. If `all_proxy` is a SOCKS
proxy, start Studio with `env -u all_proxy -u ALL_PROXY`.

**Coding agents installing EvoAB** should: check `nvidia-smi` and free disk;
clone and install as above; set up Docker GPU access, asking before every
`sudo`; start Studio in tmux (next section); run `evoab benchmarks check`; then
hand over the Studio URL. Never ask for, type or store API keys: the user enters
them in Studio.

## Models

The built-in agent (`--agent native`, the default) calls models through
**presets**. A preset names a provider (base URL, protocol, key) and a model ID
with its limits and reasoning effort. Presets, providers and recorded usage live
in `~/.config/evoab/agent/` (keys mode 600) and are managed in Studio
(设置 → 模型) or with the CLI:

```bash
evoab models list
evoab models add-provider "My API" --base-url https://host/v1 --key-env MY_API_KEY
evoab models add-preset my-model --provider my_api --model MODEL_ID --effort medium
evoab usage
```

The CLI reads keys only from an environment variable or standard input, never
from arguments. Studio launches every method on the built-in agent. Other agent
runtimes (Codex, Claude Code, OpenCode, Gemini CLI, Antigravity) remain available
from the command line with `--agent`; add `--model-preset` so `--model` names a
preset and the agent takes its model ID, base URL, protocol and key from it.

## Studio

```bash
evoab studio --runs-root ~/evoab-runs --port 8770
# keep it running, e.g.:
tmux new-session -d -s evoab-studio "evoab studio --runs-root ~/evoab-runs --port 8770"
```

Open `http://<host>:8770` (forward the port for a remote machine). The header
shows the commit Studio runs and turns amber when that build has local changes,
is unknown, or was updated while the page was open.

- **发起任务**: choose run type, benchmark, task, method, model, then confirm.
  RoboDojo tasks are grouped by capability dimension; generalization tasks offer
  standard and random layouts; seed and layout pick the evaluation scene
  ([benchmarks](benchmarks.md#robodojo)). The method step shows each method's
  component graph and parameters; Agent team lets you compose roles from cards.
- **运行记录**: running and finished runs, replay step by step, token and cost.
- **设置**: 概览 (usage), 模型 (providers and presets), 评测集 (deploy, check and
  remove benchmark runtimes).

Everything Studio does is also a command backed by the same code; add `--json`
for the API payload. The HTTP API is described at `/openapi.json`.

```bash
evoab benchmarks list
evoab benchmarks tasks robodojo
evoab runs launch --integration robodojo --task stack_bowls --method agentic --model my-model
evoab runs list
evoab runs trace RUN_ID            # rounds, tool calls, results and why the run ended
evoab runs trace RUN_ID --round 4  # full input and output of one model request
evoab runs stop RUN_ID
```

## Run from the command line

One `evoab run` is one episode. Either let EvoAB start the environment
(`--deploy local`, from a prepared runtime) or connect to a running one
(`--env-url`).

```bash
evoab run --integration robodojo --deploy local --task stack_bowls \
  --recipe agentic --model my-model --no-sam3 --output-dir ./runs --monitor

evoab run --integration robodojo --env-url http://127.0.0.1:18760 \
  --recipe agentic --model my-model --no-sam3
```

| Flag | Meaning |
|---|---|
| `--integration` | Required: which environment adapter |
| `--recipe` | Method id, method directory or recipe file (default: the integration's default method) |
| `--set NAME=VALUE` | Override a declared parameter or any dotted recipe path |
| `--model`, `--reasoning-effort`, `--max-turns` | Override the entry role's brain |
| `--task`, `--seed`, `--layout-id` | Which task and evaluation scene (local deploy), or identity checks (`--env-url`) |
| `--no-depth`, `--no-camera-calibration` | Observation setting of a local environment (RoboDojo captures depth by default) |
| `--no-sam3` / `--sam3-url` | Disable SAM3 or point at a running SAM3 service |
| `--container` | Run the environment in its Docker runtime |
| `--episode-timeout-s` | Episode wall-clock budget (default 10800) |
| `--visible-tools NAME…` | Expose only these tools (one-role tools/code methods) |
| `--obs-pipeline FILE` | Attach an observation pipeline (SAM3, FoundationPose) in front of the environment |
| `--monitor` | Serve a live web console for this run |
| `--validate-config`, `--print-config` | Check or print the resolved configuration offline, then exit |

`evoab run --integration X --help` lists the integration's own options. `evoab
tui` runs one task interactively against an `--env-url`; `evoab deploy env`
starts a prepared environment in the foreground for it.

**Outcomes.** Tool success, agent completion and task success are separate.
The agent ends with `finish`, which returns only an acknowledgement; the
environment decides success, and the agent never sees the verdict.

## Methods and recipes

A recipe names one or more **roles** and the **loop** connecting them. Each role
has a brain (model and effort; the agent defaults to the run's), a **channel**,
**capabilities** and a **context** policy.

| Channel | The role reaches the robot by |
|---|---|
| `tools` | Native tool calls, reading each result before deciding |
| `code` | Python programs against a generated robot API: Codex and Claude Code run them in their own terminal, every other agent through the `run_program` tool |
| `action` | One stateless model request per action chunk (`abs_eef`, `delta_eef` or `joint_abs`), executed by the environment's controller |

```yaml
id: planner_direct
label: Planner + Direct controller
summary: A tool-calling planner splits the task; a Direct controller executes each sub-goal.
paradigms: [orchestrate, direct]
status: experimental
roles:
  planner:
    channel: tools
    capabilities:
      - skills
      - {id: scene, tools: [view_env_state, capture_scene]}
      - episode
      - {id: delegate, options: {roles: [controller]}}
    prompt: {append: "Split the task into short, verifiable sub-goals …"}
  controller:
    channel: action
    action: {representation: abs_eef, horizon: [3, 15], history: [15, 7], max_calls: 6}
loop: {entry: planner}
execution: {return_mode: immediate}
parameters:
  - {name: controller_calls, path: roles.controller.action.max_calls, type: integer}
```

- **Capabilities** select tool modules (`skills`, `scene`, `gripper`, `hybrid`,
  `episode`, ...) and optionally narrow them to some tools; `{id: X, optional: true}`
  is skipped where an environment lacks it. `evoab methods components
  --integration X` lists everything a recipe can name.
- **Delegation**: `delegate` exposes other roles as `delegate_<role>(instruction)`;
  the role runs with its own brain, channel and tools against the same episode and
  returns a report.
- **Episodes and experience**: `loop.episodes.repeat` runs several episodes;
  `carry: [skills]` or `[programs]` moves them forward, `keep: success|always`
  decides when they are promoted (`--experience-dir`, default
  `<output>/experience/<method>/<integration>/`).
- **Context**: `identity` (full history), `sliding_window`, `observation_window`
  for tool and code roles; action roles see a stateless window of frames.
- **Validation is offline and strict**: unknown fields, unreachable roles,
  missing capabilities or tools fail before anything starts.

```bash
evoab methods list --integration robodojo
evoab methods show code_iterate          # card, recipe, parameters
evoab methods graph planner_direct       # nodes and edges (JSON)
evoab methods validate ./my_method/ --integration robodojo
evoab run … --recipe agentic --set roles.main.context.policy=observation_window \
  --set roles.main.capabilities.gripper.tools='[move, hold]'
```

**Add a method**: copy the closest method directory from `evoab/methods/`, edit
`method.yaml`, put method instructions in `prompt.append` or `prompts/*.md`, write
`CARD.md` (paper and code links, the paper's setting and numbers, what is and is
not reproduced, a results table), validate against every target integration, run
one episode and check `recipe.json` and `effective_prompt.md`. Run it with
`--recipe ./my_method/` or add a root with `EVOAB_METHODS_DIR`. If the method needs
a capability that does not exist, add a component ([architecture](architecture.md#extend-evoab))
rather than special-casing the method.

## Results

Each run writes a directory under `--output-dir`:

| File | Contents |
|---|---|
| `result.json` | Configuration summary, recipe, outcome (`task_success`, `agent_finished`, `accepted`, `failure_category`) |
| `recipe.json`, `effective_prompt.md`, `tools.json` | The compiled recipe, the prompt and the tool schemas the agent saw |
| `trajectory.jsonl` | Agent and tool events with image references |
| `images/`, `artifacts/` | Content-addressed images and other artifacts |
| `roles/NNN_<role>/` | Records of delegated role runs |
| `direct/` | Requests and replies of each Direct inference |

```bash
evoab monitor --run-dir ./runs --port 8765    # replay saved runs, no model needed
```

## Python SDK

```python
from evoab import AgentConfig, SessionConfig, run

result = run(SessionConfig(
    integration="robodojo", env_url="http://127.0.0.1:18760", no_sam3=True,
    recipe="planner_direct", recipe_overrides={"controller_calls": 4},
    agent=AgentConfig(model="my-model"),
))
print(result.task_success)
```

`Session(SessionConfig(...))` gives direct access to the episode's tools for a
custom strategy; `setup()` and `deploy()` prepare and start runtimes from Python.
Examples live in [`examples/sdk/`](../examples/sdk/).
