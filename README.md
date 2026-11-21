<h1 align="center">EvoAB</h1>

<p align="center"><b>Evolving Agents with Bodies</b> — an open platform for robot-use agents</p>

EvoAB brings agent-based robot manipulation methods together so they can be run,
reproduced and compared under the same environments, tools and evaluation
protocols. These methods use a general-purpose model as the policy, or combine
it with a VLA or world-action model.

> **Status: pre-alpha.** See [docs/PLATFORM_DESIGN.md](docs/PLATFORM_DESIGN.md)
> for the platform design and milestones. Interfaces will change.

## Features

- **One harness, many methods.** Direct robot tools, Hybrid control with an
  action model such as Pi0.5, a four-tool Clean baseline, and Code as Policy.
  More methods are added on top of these modes.
- **Agent runtimes.** Codex and Claude Code, plus experimental OpenCode, Gemini
  CLI and Antigravity adapters. The model gateway converts between Responses,
  Chat Completions and Messages, so one agent can use many model endpoints.
- **Realistic by default.** Agents see RGB, proprioception, calibration and robot
  descriptions; simulator depth and task-verifier feedback stay hidden unless
  enabled.
- **Run records and replay.** Every episode saves its configuration, tool calls,
  frames, outcome and SFT-ready trajectories. A local web monitor replays them.

## Environments

| Type | Integration | Notes |
|---|---|---|
| Simulation | RoboDojo | Dual-arm manipulation; Direct tools or Hybrid Pi0.5 |
| Simulation | LIBERO-Plus / PRO | Official perturbation suites |
| Simulation | RoboTwin 2.0, RMBench, CALVIN, RoboLab, DexJoCo | See [docs/integrations.md](docs/integrations.md) |
| Simulation | BEHAVIOR, IsaacLab Arena, NaVILA-Bench | Experimental mobile, humanoid and quadruped |
| Real robot | ARX ONE, Piper-X | Dual-arm platforms with RealSense cameras |

## Quick start (RoboDojo, on a supported Linux GPU host)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
python scripts/release/prepare.py --output-dir ../dist-evoab
evoab setup --integration robodojo \
  --wheel ../dist-evoab/dist/evoab_runtime-0.2.0-py3-none-any.whl
```

Run one task with a Codex agent through any OpenAI-compatible endpoint:

```bash
export EVOAB_API_KEY=...
evoab run --integration robodojo --deploy local \
  --task stack_bowls --action-profile direct --no-sam3 \
  --agent codex --model gpt-6-astra --reasoning-effort low \
  --provider-url http://127.0.0.1:38579/v1 \
  --provider-profile omnirouter.chat_completions --codex-web-search disabled \
  --output-dir ./runs --monitor
```

Replay saved runs with `evoab monitor --run-dir ./runs`. See
[docs/getting-started.md](docs/getting-started.md) and
[docs/running.md](docs/running.md) for more.

## Direct policies and nanobot

`--agent nanobot` takes models from [nanobot](https://github.com/HKUDS/nanobot)
(`pip install 'evoab[nanobot]'`): providers, model presets, request APIs,
retries and usage accounting are nanobot's, read from the same config file its
WebUI edits, and every call appears in nanobot's usage view. `--model` names a
nanobot preset; `evoab models` lists them.

`--interaction-mode direct_policy` makes the model the controller: each
inference is one independent request (prompt pack + task + a short window of
observations) that must return exactly one action-chunk tool call, such as
`emit_eef_actions` with 3–15 absolute grasp-point poses `[x, y, z, qw, qx, qy,
qz, gripper]`. The chunk is validated and step-limited completely, then the
benchmark's own controller executes one row per control step; malformed output
is re-requested on the unchanged observation, and if every attempt fails the
robot holds for one step. No tools are called. Per-benchmark prompt packs
(`evoab/resources/prompts/direct/`) describe the embodiment and frames; timing,
gripper geometry and camera geometry are generated from what the environment
reports, and `--direct-prompt-blocks` / `--direct-prompt-file` choose or replace
them. Records go to `direct/` in the run directory, and Studio replays them step
by step with every inference's request, reply, latency and cost.

```bash
evoab run --integration libero_pro --deploy local --runtime-profile libero_object \
  --task pick_up_the_cream_cheese_and_place_it_in_the_basket --no-sam3 \
  --agent nanobot --model gpt-6-astra --interaction-mode direct_policy \
  --direct-representation abs_eef --direct-horizon 3:15 --direct-history 15:7
```

Without `direct_policy`, `--agent nanobot` runs nanobot's agent runner with only
EvoAB's robot tools registered (no nanobot workspace, memory, skills or web
tools); nanobot's transcript consolidation handles long contexts, with its
storage inside the run directory.

## Studio

EvoAB Studio is a local web page for launching prepared LIBERO-PRO tasks,
tracking running jobs, and reviewing run outcomes, token usage, and replays.
The Benchmark page lists other integrations and their setup requirements.

```bash
evoab studio --runs-root ./runs --port 8770
```

Everything Studio does is also a command, backed by the same code
(`evoab/workbench/`). Add `--json` for the exact payload the Studio API
returns; the HTTP API is described at `/openapi.json`.

```bash
evoab benchmarks tasks libero_pro --suite libero_object
evoab models                        # nanobot providers and presets
evoab runs launch --task libero_object/pick_up_the_alphabet_soup_and_place_it_in_the_basket \
  --method direct_policy --model gpt-6-astra --representation abs_eef
evoab runs list
evoab runs trace 721e018e          # rounds, tool calls, results and why the run ended
evoab runs trace 721e018e --round 4   # full input and output of one model request
evoab runs stop 721e018e
evoab providers add my-api --base-url https://host/v1 --key-env MY_API_KEY
evoab providers probe my-api some-model
evoab usage
```

For an unreleased build, set the matching runtime wheel's path on the Studio
server under Settings → General (设置 → 通用) before using one-click Benchmark
setup. See [local deployment](docs/deployment.md) for runtime wheel details.

Manage API providers and probe model capabilities under Settings (设置). The
server stores provider definitions and keys outside the repository under
`~/.config/evoab/` by default. The browser displays masked keys; the CLI reads
keys from standard input or an environment variable, never from arguments.

## Repository layout

```text
evoab/agents/        agent runtimes (Codex, Claude Code, ...)
evoab/provider/      model gateway and protocol conversion
evoab/execution/     scheduling: serial, parallel, replanning, code as policy
evoab/tools/         shared robot tools (gripper, scene, skills, episode, hybrid)
evoab/services/      environment, actor and perception services
evoab/integrations/  environment and robot adapters
evoab/ui/web/        run monitor and replay
docs/                user, developer and platform documentation
```

## Third-party components

Third-party code, fonts, external assets and models retain their respective
licenses, for example
[evoab/integrations/robodojo/UPSTREAM_LICENSE](evoab/integrations/robodojo/UPSTREAM_LICENSE).

## License

MIT. See [LICENSE](LICENSE).
