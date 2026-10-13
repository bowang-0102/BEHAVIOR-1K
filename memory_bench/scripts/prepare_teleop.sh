#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  prepare_teleop.sh sample SESSION_CONFIG
  prepare_teleop.sh finalize SESSION_CONFIG [POSE_MANIFEST]

sample registers the session, samples all Key and Query task templates, creates
the first-Key TRO set, freezes case assignments, samples full-scene instances and
entry poses, and scans source recordings. finalize writes the available manifests
and leaves calibration status pending until those checks have been performed.
EOF
  exit 2
}

[[ $# -ge 2 ]] || usage
MODE="$1"
SESSION_CONFIG="$(realpath "$2")"
shift 2

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CONDA_BIN="${CONDA_EXE:-/data2/bwang/miniconda3/condabin/conda}"
[[ -x "$CONDA_BIN" ]] || { echo "Conda executable not found: $CONDA_BIN" >&2; exit 1; }

mapfile -t SESSION_FIELDS < <(
  "$CONDA_BIN" run --no-capture-output -n behavior python - "$SESSION_CONFIG" <<'PY'
import sys
import yaml

with open(sys.argv[1]) as stream:
    config = yaml.safe_load(stream)
print(config["session_id"])
print(config["paths"]["data_root"])
print(config["paths"]["output_root"])
print(config["scene_model"])
print(config["instance_sampling"]["count"])
segments = config["segments"]
for segment_id in config["collection"]["key_segment_ids"]:
    print(segments[segment_id]["activity"])
query_id = next(key for key, segment in segments.items() if segment["role"] == "query_task")
print("__QUERY__:" + segments[query_id]["activity"])
PY
)

SESSION_ID="${SESSION_FIELDS[0]}"
DATA_ROOT="${SESSION_FIELDS[1]}"
OUTPUT_ROOT="${SESSION_FIELDS[2]}"
SCENE_MODEL="${SESSION_FIELDS[3]}"
INSTANCE_COUNT="${SESSION_FIELDS[4]}"
KEY_ACTIVITIES=()
QUERY_ACTIVITY=""
for (( index=5; index<${#SESSION_FIELDS[@]}; index++ )); do
  if [[ "${SESSION_FIELDS[$index]}" == __QUERY__:* ]]; then
    QUERY_ACTIVITY="${SESSION_FIELDS[$index]#__QUERY__:}"
  else
    KEY_ACTIVITIES+=("${SESSION_FIELDS[$index]}")
  fi
done
[[ ${#KEY_ACTIVITIES[@]} -gt 0 && -n "$QUERY_ACTIVITY" ]] || {
  echo "Session config must define at least one Key and one Query activity." >&2
  exit 1
}
FIRST_KEY_ACTIVITY="${KEY_ACTIVITIES[0]}"
TASK_DATASET="memory-bench-task-instances"
TASK_JSON_DIR="$DATA_ROOT/$TASK_DATASET/scenes/$SCENE_MODEL/json"
SESSION_ROOT="$OUTPUT_ROOT/sessions/$SESSION_ID"
SAMPLED_ROOT="$SESSION_ROOT/sampled"
POSE_MANIFEST="$SAMPLED_ROOT/poses.json"
SCENE_ROOT="$SAMPLED_ROOT/scene_snapshots"
INVENTORY="$OUTPUT_ROOT/reports/$SESSION_ID/source_inventory.json"

export OMNIGIBSON_DATA_PATH="$DATA_ROOT"
export OMNIGIBSON_TASK_INSTANCES_DATASET="$TASK_DATASET"
export MB_ROOT="$OUTPUT_ROOT"
export PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONBREAKPOINT=0

run_python() {
  "$CONDA_BIN" run --no-capture-output -n behavior python "$@"
}

case "$MODE" in
  sample)
    [[ $# -eq 0 ]] || usage
    if [[ -e "$SESSION_ROOT/case_assignments.json" || -e "$POSE_MANIFEST" || -e "$INVENTORY" ]]; then
      echo "Sampling output already exists under $SESSION_ROOT; use a fresh output_root for a new sample run." >&2
      exit 1
    fi

    export OMNIGIBSON_HEADLESS=1
    run_python "$REPO_ROOT/memory_bench/collection/prepare_task.py" "$SESSION_CONFIG" init

    for ACTIVITY in "${KEY_ACTIVITIES[@]}" "$QUERY_ACTIVITY"; do
      run_python "$REPO_ROOT/OmniGibson/scripts/sampling/sample_b1k_tasks.py" \
        --activity "$ACTIVITY" --output_dir "$TASK_JSON_DIR"
      if [[ "$ACTIVITY" == "$FIRST_KEY_ACTIVITY" ]]; then
        run_python "$REPO_ROOT/OmniGibson/scripts/sampling/multiply_b1k_tasks.py" \
          --activity "$ACTIVITY" --start_idx 1 --end_idx "$INSTANCE_COUNT" \
          --partial_save --headless --output_dir "$TASK_JSON_DIR"
      fi
      mkdir -p "$TASK_JSON_DIR/${SCENE_MODEL}_task_${ACTIVITY}_instances"
      run_python "$REPO_ROOT/OmniGibson/scripts/sampling/sample_robot_pose.py" \
        --activity "$ACTIVITY" --output_dir "$TASK_JSON_DIR"
    done

    run_python "$REPO_ROOT/memory_bench/collection/prepare_task.py" "$SESSION_CONFIG" freeze-cases
    run_python "$REPO_ROOT/memory_bench/collection/sample_instances.py" "$SESSION_CONFIG"
    run_python "$REPO_ROOT/memory_bench/processing/sources.py" "$SESSION_CONFIG"
    cat <<EOF
Sampled session $SESSION_ID ($INSTANCE_COUNT instances; first Key: $FIRST_KEY_ACTIVITY).
Scene snapshots: $SCENE_ROOT
Pose manifest:   $POSE_MANIFEST
Source inventory: $INVENTORY

Perform representative and physical calibration when available, then call finalize
with the resulting pose manifest. Unperformed checks remain marked pending.
EOF
    ;;
  finalize)
    [[ $# -le 1 ]] || usage
    CALIBRATED_POSES="${1:-$POSE_MANIFEST}"
    [[ -f "$CALIBRATED_POSES" ]] || { echo "Pose manifest not found: $CALIBRATED_POSES" >&2; exit 1; }
    [[ -d "$SCENE_ROOT" ]] || { echo "Sampled scene snapshots not found: $SCENE_ROOT" >&2; exit 1; }
    [[ -f "$INVENTORY" ]] || { echo "Source inventory not found: $INVENTORY; run sample first." >&2; exit 1; }
    run_python "$REPO_ROOT/memory_bench/collection/prepare_task.py" "$SESSION_CONFIG" finalize \
      --pose-manifest "$CALIBRATED_POSES" --scene-root "$SCENE_ROOT" --inventory "$INVENTORY"
    echo "Instance manifests written for $SESSION_ID; calibration readiness is recorded in their status."
    ;;
  *)
    usage
    ;;
esac
