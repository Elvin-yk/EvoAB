# Code

The agent writes ordinary Python programs against a generated robot API and runs
them with the `run_program` tool. Programs call the same registered tools with
the same validation and scheduling. Without `tools` in `act`, only skill reading
stays a direct call; with it, every tool is callable both ways.

| | |
|---|---|
| Acts through | `code` (combine with `tools` and `action` in `act`) |
| Context | Full transcript |

## Reproduction targets

- Code as Policies / CaP-X (training-free): multi-turn programs with execution
  feedback. Planned: a visual-difference helper in the robot API.
- RAX (partial): programs kept across episodes and promoted after successes:
  `loop.episodes: {repeat: 5, carry: [programs], keep: success}` (see
  `examples/recipes/code_iterate.yaml`). Not reproduced: RAX's frontend/backend
  split, parallel exploration and its skills library.

## Results

No benchmark numbers recorded in this card yet.
