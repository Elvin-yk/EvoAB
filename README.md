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
| Simulation | RoboUse | LIBERO, RoboCasa, MetaWorld, RoboSuite, ManiSkill, Tabletop and more |
| Simulation | LIBERO-Plus / PRO | Official perturbation suites |
| Simulation | RoboTwin 2.0, RMBench, CALVIN, RoboLab, DexJoCo | See [docs/integrations.md](docs/integrations.md) |
| Simulation | BEHAVIOR, IsaacLab Arena, NaVILA-Bench | Experimental mobile, humanoid and quadruped |
| Real robot | ARX ONE, Piper-X | Dual-arm platforms with RealSense cameras |

## Quick start (RoboUse, MuJoCo)

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
python scripts/release/prepare.py --output-dir ../dist-evoab
evoab setup --integration robouse --runtime-profile mujoco \
  --wheel ../dist-evoab/dist/evoab_runtime-0.2.0-py3-none-any.whl
```

Run one task with a Codex agent through any OpenAI-compatible endpoint:

```bash
export EVOAB_API_KEY=...
evoab run --integration robouse --deploy local --runtime-profile mujoco \
  --task libero-red-block-in-bowl --action-profile direct --no-sam3 \
  --agent codex --model gpt-6-astra --reasoning-effort low \
  --provider-url http://127.0.0.1:38579/v1 \
  --provider-profile omnirouter.chat_completions --codex-web-search disabled \
  --output-dir ./runs --monitor
```

Replay saved runs with `evoab monitor --run-dir ./runs`. See
[docs/getting-started.md](docs/getting-started.md) and
[docs/running.md](docs/running.md) for more.

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
