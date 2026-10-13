#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONDA_BIN="${CONDA_EXE:-/data2/bwang/miniconda3/condabin/conda}"
cd "$REPO_ROOT"
exec "$CONDA_BIN" run --no-capture-output -n behavior python joylo/scripts/run_joylo.py \
  --robot-port 6001 \
  --hostname 127.0.0.1 \
  --hz 100 \
  --gello-model r1pro \
  --gello-name default \
  --gello-port "${GELLO_PORT:-/dev/ttyUSB1}" \
  --damping-motor-kp 0.0 \
  --use-joycons
