# Code as Policy

The agent uses its native shell to run ordinary Python programs against a
generated `scratch/robot_api.md`. Programs call the same registered tools with
the same validation and scheduling. Skill tools stay directly callable.

Runs on any agent: Codex and Claude Code run programs in their own terminal; other agents use the harness `run_program` tool.

## Reproduction targets

- CaP-X (CaP-Agent0, training-free): multi-turn interaction, execution feedback
  and visual differencing. Planned: visual-difference helper in the robot API.
- See `code_iterate` for experience carried across episodes.
