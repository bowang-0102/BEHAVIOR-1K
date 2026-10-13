#!/usr/bin/env bash
# Shared environment for the headless pipeline scripts.
set -euo pipefail

MB_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MB_CONFIG="$(realpath "${1:-$MB_SCRIPT_DIR/../configs/mb_retrieve_mug_from_top_cabinet.yaml}")"
CONDA_BIN="${CONDA_EXE:-/data2/bwang/miniconda3/condabin/conda}"
cd "$MB_SCRIPT_DIR/../.."
export OMNIGIBSON_HEADLESS=1 PYTHONBREAKPOINT=0
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

run_python() {
  "$CONDA_BIN" run --no-capture-output -n behavior python "$@"
}

MB_FIELDS="$(run_python - "$MB_CONFIG" <<'PY'
import sys
from memory_bench.session import Session
session = Session.load(sys.argv[1])
print(session.session_id)
print(session.paths.data_root)
print(session.paths.output_root)
print(session.config["scene_model"])
print(session.config["instance_sampling"]["count"])
for key in [*session.key_ids, session.query_id]:
    print(session.segments[key]["activity"])
PY
)"
mapfile -t MB_FIELDS <<< "$MB_FIELDS"
MB_SESSION_ID="${MB_FIELDS[0]}"
export OMNIGIBSON_DATA_PATH="${MB_FIELDS[1]}" MB_ROOT="${MB_FIELDS[2]}"
export OMNIGIBSON_TASK_INSTANCES_DATASET=memory-bench-task-instances
