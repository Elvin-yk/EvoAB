# Planner + Direct controller

A composition of the orchestrate and direct paradigms. The planner keeps a full
transcript, sees calibrated captures and decides *what* to do; the controller is
stateless and decides *how* to move, one sub-goal at a time, from recent frames.

| Role | Brain | Channel | Capabilities |
|---|---|---|---|
| planner (entry) | native | tools | skills, scene (state, capture), episode, delegate → controller |
| controller | native | action (abs EEF) | — |

`delegate_controller` occupies the episode resource while the controller runs, so
no other robot action can interleave. The controller's records are under
`roles/NNN_controller/direct/` in the run directory.

Status: experimental composition; no benchmark numbers yet.
