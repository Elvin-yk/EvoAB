# PhysicalRSI in EvoAB

## Run the PhysicalRSI agent

In Studio, select a prepared manipulation simulator and task. Choose
**Agent 选择工具（Tool） → PhysicalRSI** in the method step, then select a model
preset and launch the run. Studio uses its regular run history and replay.
The agent composes typed observation and decision skills through PhysicalRSI,
and dispatches selected tools through EvoAB's session executor.

Install with `pip install 'evoab[agent,physicalrsi]'`. The extra pins the
upstream PhysicalRSI 0.3.0 revision. For an equivalent single task from CLI:

```bash
evoab run --integration metaworld --deploy local --task reach-v3 \
  --agent physicalrsi --model YOUR_PRESET --interaction-mode tool_calling --no-sam3
```

The agent entry runs one episode using System 1. Use the research CLI to manage
the separate multi-episode System 2 workflow and its existing records.

## Multi-episode campaigns through the CLI

EvoAB integrates PhysicalRSI 0.3.0's original `ResearchCampaign`, `SelfHarness`,
`ExperimentEvaluator`, `LanguageMutationEvolver`, paired-permutation selection,
`HarnessState` and `ResearchMemory`. These upstream algorithms are called
without modifications. This workflow evolves candidates and research memory
across multiple episodes; it is not the frozen RoboDojo baseline.

The optional `physicalrsi` dependency installs the original 0.3.0 core from
upstream Git commit `2574f72bebf9b357140ebe376db71e782575fe65`; this release
is not currently available on PyPI. Native simulator assets and model weights
remain separate deployment resources.

Prepare a manipulation simulator and configure a model preset. A research launch
starts model calls and simulator trials for development, paired validation, and
a held-out test.

```bash
evoab research launch --integration metaworld --task reach-v3 \
  --model YOUR_PRESET --rounds 2 --candidates 1 \
  --development-cases 1 --validation-cases 3 --test-cases 1 \
  --max-turns 8 --episode-seconds 600 --max-trials 15
evoab research list --json
evoab research stop research_ID
evoab research resume research_ID
```

The research CLI and existing HTTP clients share Workbench operations:

- `GET /api/research`
- `POST /api/research`, JSON fields from `ResearchConfig`
- `POST /api/research/{id}/stop`
- `POST /api/research/{id}/resume`

## Execution and evolution boundaries

System 1 composes typed PhysicalRSI skills for public scene preparation and
model tool selection. The policy receives RGB sensor blobs, robot state,
calibration/tool feedback and the instruction. Only the EvoAB environment
adapter executes registered tools. EvoAB owns environment services and finalizes
each episode; the PhysicalRSI verifier reads the official outcome afterwards.
The task verdict is never supplied to the executing policy.

System 2 uses development receipts to propose bounded mutations to the declared
control surface: motion steps per call (1–12), approach clearance (0.01–0.15 m)
and recent history length (1–8 turns). Each candidate is separately materialized
and admitted. The adapter explicitly declares fresh paired validation as a
required candidate check; upstream statistical selection owns inheritance.
Each research round also asks the model for a testable hypothesis, which the
original ResearchMemory API binds to verified development evidence and stores
as a new immutable revision. Memory is available to subsequent proposals.
Hypotheses are not presented as proven improvements.

The initial binding uses EvoAB's existing `clean` tools. It is a new environment
adapter for the general PhysicalRSI core, not a claim of reproducing the separate
RoboDojo leaderboard submission. The active search surface is explicitly bounded;
this integration does not train VLA weights or execute arbitrary model-generated
Python. Additional skill providers or training evolvers can extend that surface
through PhysicalRSI's existing contracts and should be independently validated.

Development and validation seeds are distinct in every round. Final-test seeds
remain fixed, are claimed only after campaign completion, and are not provided
to reflection or subsequent rounds. Seed separation is a reproducibility boundary,
not proof of statistical independence between physical layouts. Small validation
cohorts are useful for wiring checks but rarely justify promoting a candidate.

## Evidence, persistence and interruption

Campaigns live at `<runs-root>/.studio/research/research_ID/`, including request,
implementation identities, bounded quota, per-round proposals, paired evaluation,
selection, lineage, research memory and final-test evidence. Original engine
version and hashes plus adapter hashes are pinned. Native EvoAB episode records
remain in run history, linked by `research_trial.json` to the parent campaign.
The research CLI lists per-round decisions, skill and memory revisions, learned
hypotheses, logs and held-out metrics. Replay uses the ordinary episode artifacts.

Stopping interrupts the worker, closes owned sessions and simulator process groups,
and preserves receipts. Resume invokes the upstream audit and reuses completed
stages. An uncertain model call, physical effect or reflection remains blocked
for reconciliation; it is not automatically replayed. Resume cannot silently
change the round count, model configuration, source implementation or seed plan.
Infrastructure faults and task failures remain separate.

Only prepared simulation integrations are admitted by this adapter. Hardware
operation and external checkpoint deployment require separate integration work.
Benchmark dependencies, assets and model credentials are not bundled with the
PhysicalRSI core. All model calls use EvoAB's configured provider catalog and
usage recorder; keys are not embedded in requests saved as campaign configuration.

See the [PhysicalRSI upstream project](https://github.com/PhysicalRSI/PhysicalRSI).

Completed research is never required to promote a candidate. Retaining the
incumbent after unsuccessful or statistically inconclusive trials is a valid
outcome, and learned hypotheses remain distinct from validated improvements.

Research episode token counts are recorded in PhysicalRSI verifier measurements;
run-history rows link to the research report rather than displaying a misleading
zero from the SDK's tool-only trace format. The model settings overview retains
global API accounting, including proposal and reflection calls.

Use `evoab research recycle ID...` to move a finished campaign to the recycle
bin. Existing campaign entries appear under **历史研究**. Restore and permanent
deletion operate on the campaign record, and referenced trial evidence stays
protected while its campaign is retained. See [recycle-bin behavior](recycle-bin.md).
