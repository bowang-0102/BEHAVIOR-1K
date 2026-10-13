#!/usr/bin/env bash
# Usage: collect.sh [INSTANCE_ID=1] [SESSION_CONFIG] [RECORDING_PATH]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONDA_BIN="${CONDA_EXE:-/data2/bwang/miniconda3/condabin/conda}"
SESSION_CONFIG="$(realpath "${2:-$SCRIPT_DIR/../configs/mb_retrieve_mug_from_top_cabinet.yaml}")"
RECORDING_ARGS=()
if [[ -n "${3:-}" ]]; then
  RECORDING_ARGS=(--session-recording-path "$3")
fi

export DISPLAY="${DISPLAY:-:0}" OMNIGIBSON_HEADLESS=0
unset OMNIGIBSON_REMOTE_STREAMING
if [[ -z "${XAUTHORITY:-}" && -r "$HOME/.Xauthority" ]]; then
  export XAUTHORITY="$HOME/.Xauthority"
fi

exec "$CONDA_BIN" run --no-capture-output -n behavior python "$SCRIPT_DIR/../../joylo/scripts/launch_og.py" \
  --session-config "$SESSION_CONFIG" --session-instance-id "${1:-1}" "${RECORDING_ARGS[@]}"
