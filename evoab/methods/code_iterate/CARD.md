# Code iteration across episodes

A code-channel agent runs a sequence of episodes. Each episode starts from the
program library kept so far (`scratch/programs/`), and the library is promoted
only after an officially successful episode (configurable). The experience
directory defaults to `<output>/experience/code_iterate/<integration>/`;
`episodes.jsonl` there logs every attempt.

## Reproduction target: RAX (partial)

RAX scales online exploration and distills successful exploration into
standalone programs. This recipe reproduces the cross-episode program library
and success-gated promotion. Not yet reproduced: RAX's frontend/backend split,
parallel exploration and its skills library.

## Results

| benchmark | protocol | model | episodes | final success | runs |
|---|---|---|---|---|---|
| — | — | — | — | — | — |
