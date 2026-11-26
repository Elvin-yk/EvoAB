# Agentic Policy

The default EvoAB method: one agent in a persistent session observes the scene,
calls robot tools (move, grasp, release, hold, locate, capture) and decides from
their feedback. Background tool returns let reasoning overlap motion.

This is the harness of *Democratizing Robot-Use Agents: A Unified Harness with
Open Models* (RUA, arXiv 2611.01560, code: github.com/Open-RUA/uni-robot-agent).
EvoAB grew from that code base, so RUA's shared tools, overlapped execution and
realistic observations are EvoAB's own components rather than a separate method.

| | |
|---|---|
| Paradigm | Orchestrate |
| Channel | Tool calls |
| Capabilities | skills, scene, gripper, episode |
| Context | Full transcript (configurable) |

## RUA paper setting

- Realistic observations: RGB, proprioception, calibration, robot description and
  instruction; no simulator depth, CAD assets or evaluator feedback. On RoboDojo
  start the environment with depth disabled (`--no-depth`).
- RoboDojo: 42 tasks × 3 seeds × 2 layouts (252 episodes).
- Context: the paper evicts old images, then compresses history
  (`--set context=observation_window` or `sliding_window`).
- The paper adds brief task rules: `--include-extra-task-requirements` (off by
  default, as in the official Agent Track protocol).

```bash
evoab run --integration robodojo --deploy local --no-depth --include-extra-task-requirements \
  --task stack_bowls --recipe agentic --model my-model --no-sam3
```

Paper results on RoboDojo:

| model | harness | success | score | cost/episode |
|---|---|---|---|---|
| Qwen3.8-Flash-Next | RUA | 25.00 | 31.39 | $1.09 |
| GPT-6 Astra | RUA | 46.67 | 51.90 | $21.29 |
| GPT-6 Astra | RoboProbe | 22.48 | 28.97 | — |

Reviewer notes: the headline open-model comparison crosses harnesses (Qwen+RUA vs
Astra+RoboProbe); with six rollouts the 95% interval is about 8 points wide.

## Other reproduction notes

- Astranomy's "Agentic" form (one absolute EEF target held 1–5 steps) is a different
  tool contract; see `direct_policy` for its Direct form.

## Results

No benchmark numbers recorded in this card yet. Add rows as
`| benchmark | protocol | model | success | tokens/episode | cost/episode | runs |`.
