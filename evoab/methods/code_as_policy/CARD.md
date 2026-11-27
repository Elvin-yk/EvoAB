# Code as Policy

The agent writes ordinary Python programs against a generated robot API and runs
them with the `run_program` tool. Programs call the same registered tools with
the same validation and scheduling. Skill tools stay directly callable.

## Reproduction targets

- CaP-X (CaP-Agent0, training-free): multi-turn interaction, execution feedback
  and visual differencing. Planned: visual-difference helper in the robot API.
- See `code_iterate` for experience carried across episodes.
