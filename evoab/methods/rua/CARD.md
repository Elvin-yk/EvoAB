# RUA (Robot-Use Agent)

Native EvoAB recipe for *Democratizing Robot-Use Agents: A Unified Harness with
Open Models* (arXiv 2611.01560, code: github.com/Open-RUA/uni-robot-agent). The
paper's harness is the code base EvoAB grew from, so the method is a recipe over
the same tools rather than an adapter: shared skills, scene (calibrated capture,
2D segmentation, triangulation), gripper motion and `finish`, with background tool
returns so reasoning overlaps motion.

## Paper setting

- Realistic observations: RGB, proprioception, calibration, robot description and
  instruction; no simulator depth, CAD assets or evaluator feedback. On RoboDojo
  start the environment with depth disabled (`--no-depth`).
- RoboDojo: 42 tasks × 3 seeds × 2 layouts (252 episodes).
- Context: old images evicted, then history compressed (`--set context=…`).
- The paper adds brief task rules; EvoAB exposes them as
  `--include-extra-task-requirements` (off by default, as in the official Agent
  Track protocol). Turn them on to reproduce the paper setting.

## Paper results (RoboDojo)

| model | harness | success | score | cost/episode |
|---|---|---|---|---|
| Qwen3.8-Flash-Next | RUA | 25.00 | 31.39 | $1.09 |
| GPT-6 Astra | RUA | 46.67 | 51.90 | $21.29 |
| GPT-6 Astra | RoboProbe | 22.48 | 28.97 | — |

Reviewer notes: the headline open-model comparison crosses harnesses (Qwen+RUA vs
Astra+RoboProbe); with six rollouts the 95% interval is about 8 points wide.

## Our results

| benchmark | protocol | model | success | tokens/episode | cost/episode | runs |
|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — |

Differences from the paper code: none intended; compare with `rua_upstream`.
