# RUA (upstream package)

Runs the original `uni-robot-agent` package (https://github.com/Open-RUA/uni-robot-agent)
as an external evaluator against EvoAB's prepared RoboDojo episode. Use it only to
compare the native [`rua`](../rua/CARD.md) method with the code the paper was run
with; the evaluator owns its own planner and tool client, so recipes cannot
change its roles or tools. Install and model settings: `docs/rua.md`.
