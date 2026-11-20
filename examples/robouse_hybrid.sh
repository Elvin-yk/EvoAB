#!/usr/bin/env bash
# Connect to a prepared LIBERO or RoboCasa environment with admitted capabilities.
# Supply --model and --action-model-checkpoint-sha256 for a compatible Cartesian policy.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"
exec "${PYTHON:-python}" -m evoab run \
  --integration robouse \
  --env-url "${EVOAB_ENV_URL:-http://127.0.0.1:18760}" \
  --action-model-url "${EVOAB_ACTION_MODEL_URL:-http://127.0.0.1:18761}" \
  --action-profile hybrid --action-model-binding robouse_cartesian \
  --no-sam3 --output-dir ./runs/robouse-hybrid "$@"
