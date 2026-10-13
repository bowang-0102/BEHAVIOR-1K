#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 SESSION_CONFIG INSTANCE_ID [RECORDING_PATH]" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONDA_BIN="${CONDA_EXE:-/data2/bwang/miniconda3/condabin/conda}"
SESSION_CONFIG="$(realpath "$1")"
INSTANCE_ID="$2"
RECORDING_PATH="${3:-}"

export DISPLAY="${DISPLAY:-:0}"
export OMNIGIBSON_HEADLESS=0
unset OMNIGIBSON_REMOTE_STREAMING

if [[ -z "${XAUTHORITY:-}" && -r "${HOME}/.Xauthority" ]]; then
  export XAUTHORITY="${HOME}/.Xauthority"
fi

if [[ -n "$RECORDING_PATH" ]]; then
  exec "$CONDA_BIN" run --no-capture-output -n behavior python "$REPO_ROOT/joylo/scripts/launch_og.py" \
    --session-config "$SESSION_CONFIG" \
    --session-instance-id "$INSTANCE_ID" \
    --session-recording-path "$RECORDING_PATH"
fi

exec "$CONDA_BIN" run --no-capture-output -n behavior python "$REPO_ROOT/joylo/scripts/launch_og.py" \
  --session-config "$SESSION_CONFIG" \
  --session-instance-id "$INSTANCE_ID"
