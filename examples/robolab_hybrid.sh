#!/usr/bin/env bash
# Connect to a prepared DROID environment and an actor with the declared DROID profile.
# Supply --model and --action-model-checkpoint-sha256; add --validate-config offline.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON:-python}" -m evoab run \
  --integration robolab --task BananaInBowlTask \
  --env-url "${EVOAB_ENV_URL:-http://127.0.0.1:18770}" \
  --action-model-url "${EVOAB_ACTION_MODEL_URL:-http://127.0.0.1:18761}" \
  --action-profile hybrid --action-model-binding robolab_droid_joint \
  --no-sam3 --output-dir ./runs/robolab-hybrid "$@"
