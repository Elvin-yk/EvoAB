<h1 align="center">EvoAB</h1>

<p align="center"><b>Evolving Agents with Bodies</b> — an open platform for robot-use agents</p>

**Any LLM-agent robotics method becomes a short recipe that runs with any model,
on any simulator or real robot, and composes freely with other methods.**

Robot-use agents keep a frozen general-purpose model and differ in the harness
around it: what the model sees, whether it acts through tool calls, code or
direct action chunks, whether a VLA or world-action model is one of its tools,
and whether experience is kept across episodes. EvoAB turns those choices into
composable building blocks, so methods can be reproduced, compared and combined
under the same environments and evaluation.

> **Status: pre-alpha.** Interfaces change. Planned work is on the
> [task board](https://github.com/Elvin-yk/EvoAB/issues/11) and the
> [method roadmap](https://github.com/Elvin-yk/EvoAB/issues/34).

## Quickstart

Linux with Python 3.11. MetaWorld needs no GPU and no Docker.

```bash
git clone https://github.com/Elvin-yk/EvoAB.git && cd EvoAB
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[agent]'

# One OpenAI-compatible provider and one model preset (the key is read from the environment).
export MY_API_KEY=...
evoab models add-provider "My API" --base-url https://host/v1 --key-env MY_API_KEY
evoab models add-preset my-model --provider my_api --model MODEL_ID

evoab setup --integration metaworld
evoab run --integration metaworld --task reach-v3 \
  --recipe agentic --model my-model --output-dir ./runs
evoab studio --runs-root ./runs          # open http://127.0.0.1:8770 to replay
```

Prefer a coding agent? Paste this into Claude Code, Codex or similar on the target machine:

```text
Install EvoAB on this machine by following the "Install" section of
https://github.com/Elvin-yk/EvoAB/blob/main/docs/guide.md
Ask me before any command that needs sudo, and tell me the Studio URL when done.
```

## Methods

A method is a directory with `method.yaml` (the recipe) and `CARD.md` (paper,
setting, results). Any agent and any model preset runs any method; only external
evaluators bring their own agent.

| Method | Paradigm | How it acts |
|---|---|---|
| `agentic` | orchestrate | Tool calls: skills, scene, motion, finish |
| `clean` | orchestrate | Four tools, compact results |
| `direct_action` | direct | One action chunk per stateless inference ([Astranomy](https://research.nvidia.com/labs/srl/projects/astranomy/) Direct) |
| `code_as_policy` | code | Python programs against the robot API |
| `agent_policy` | orchestrate + policy | Tool calls plus a VLA actor |
| `planner_direct` | orchestrate + direct | A planner delegates sub-goals to a Direct controller |
| `tool_team` | orchestrate | Studio agent cards: roles, tools and delegation |
| `code_iterate` | code | Programs carried across episodes |
| `q0_preview_upstream` | orchestrate | Qoo Tech's Q0-Preview as an external evaluator |

```bash
evoab methods list --integration robodojo     # ids and where each method.yaml lives
evoab run --integration robodojo --task stack_bowls \
  --recipe planner_direct --model my-model --set roles.controller.action.max_calls=4
```

## Environments

| Type | Integrations |
|---|---|
| Simulation | RoboDojo, MetaWorld, LIBERO / LIBERO-Plus / LIBERO-PRO, RoboTwin 2.0, RMBench, CALVIN, RoboLab, DexJoCo |
| Experimental | BEHAVIOR (R1Pro), IsaacLab Arena (G1) |
| Real robot | ARX ONE, Piper-X |

Benchmarks with a container recipe deploy as Docker runtimes in one step
(Studio: 设置 → 评测集 → 部署).

## Documentation

| Document | For |
|---|---|
| [Guide](docs/guide.md) | Install, models, Studio, running from the command line, methods and recipes, results, Python SDK |
| [Architecture](docs/architecture.md) | How EvoAB is built and how to extend it: components, tools, environments, robots, agents |
| [Benchmarks](docs/benchmarks.md) | Preparing each simulator and real robot |
| [AGENTS.md](AGENTS.md) | Development rules for people and coding agents working on this repository |

## Contributing

Tasks are posted as issues on the [task board](https://github.com/Elvin-yk/EvoAB/issues/11);
anyone can claim one. Read [AGENTS.md](AGENTS.md) first. Develop from a source
checkout with `pip install -e '.[dev,agent]'`, add focused tests for new
behavior, and open pull requests against current `main` that link their issue.
Report bugs with the command, `evoab doctor --json` output and the run
directory's `result.json`; never paste credentials.

## License

MIT, see [LICENSE](LICENSE). Third-party code, assets and models keep their own
licenses, for example [RoboDojo](evoab/integrations/robodojo/UPSTREAM_LICENSE);
provenance is recorded in each integration's `SOURCE.md`.
