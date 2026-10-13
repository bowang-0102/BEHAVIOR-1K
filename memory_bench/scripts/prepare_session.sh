#!/usr/bin/env bash
# Usage: prepare_session.sh [SESSION_CONFIG] [all|sample|finalize] [POSE_MANIFEST]
set -euo pipefail
MODE="${2:-all}"
if [[ $# -gt 3 || ! "$MODE" =~ ^(all|sample|finalize)$ ]]; then
  echo "Usage: $0 [SESSION_CONFIG] [all|sample|finalize] [POSE_MANIFEST]" >&2
  exit 2
fi
CALIBRATED_POSES="${3:-}"
if [[ -n "$CALIBRATED_POSES" ]]; then
  CALIBRATED_POSES="$(realpath "$CALIBRATED_POSES")"
fi
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

SCENE_MODEL="${MB_FIELDS[3]}"
INSTANCE_COUNT="${MB_FIELDS[4]}"
ACTIVITIES=("${MB_FIELDS[@]:5}")
TASK_JSON_DIR="$OMNIGIBSON_DATA_PATH/memory-bench-task-instances/scenes/$SCENE_MODEL/json"
SESSION_ROOT="$MB_ROOT/sessions/$MB_SESSION_ID"
SAMPLED_ROOT="$SESSION_ROOT/sampled"
POSE_MANIFEST="$SAMPLED_ROOT/poses.json"
SCENE_ROOT="$SAMPLED_ROOT/scene_snapshots"
INVENTORY="$MB_ROOT/reports/$MB_SESSION_ID/source_inventory.json"

if [[ "$MODE" == all || "$MODE" == sample ]]; then
  if [[ -e "$SESSION_ROOT/case_assignments.json" || -e "$POSE_MANIFEST" || -e "$INVENTORY" ]]; then
    echo "Sampling output already exists under $SESSION_ROOT; use a fresh output_root." >&2
    exit 1
  fi
  run_python -m memory_bench.collection.prepare_task "$MB_CONFIG" init
  for ACTIVITY in "${ACTIVITIES[@]}"; do
    run_python OmniGibson/scripts/sampling/sample_b1k_tasks.py --activity "$ACTIVITY" --output_dir "$TASK_JSON_DIR"
    if [[ "$ACTIVITY" == "${ACTIVITIES[0]}" ]]; then
      run_python OmniGibson/scripts/sampling/multiply_b1k_tasks.py \
        --activity "$ACTIVITY" --start_idx 1 --end_idx "$INSTANCE_COUNT" \
        --partial_save --headless --output_dir "$TASK_JSON_DIR"
    fi
    mkdir -p "$TASK_JSON_DIR/${SCENE_MODEL}_task_${ACTIVITY}_instances"
    run_python OmniGibson/scripts/sampling/sample_robot_pose.py --activity "$ACTIVITY" --output_dir "$TASK_JSON_DIR"
  done
  run_python -m memory_bench.collection.prepare_task "$MB_CONFIG" freeze-cases
  run_python -m memory_bench.collection.sample_instances "$MB_CONFIG"
  run_python -m memory_bench.processing.sources "$MB_CONFIG"
  echo "Sampled session $MB_SESSION_ID ($INSTANCE_COUNT instances): $SAMPLED_ROOT"
fi

if [[ "$MODE" == all || "$MODE" == finalize ]]; then
  CALIBRATED_POSES="${CALIBRATED_POSES:-$POSE_MANIFEST}"
  [[ -f "$CALIBRATED_POSES" ]] || { echo "Pose manifest not found: $CALIBRATED_POSES" >&2; exit 1; }
  [[ -d "$SCENE_ROOT" ]] || { echo "Sampled scene snapshots not found: $SCENE_ROOT" >&2; exit 1; }
  [[ -f "$INVENTORY" ]] || { echo "Source inventory not found: $INVENTORY; run sample first." >&2; exit 1; }
  run_python -m memory_bench.collection.prepare_task "$MB_CONFIG" finalize \
    --pose-manifest "$CALIBRATED_POSES" --scene-root "$SCENE_ROOT" --inventory "$INVENTORY"
  echo "Instance manifests written for $MB_SESSION_ID; unperformed calibration checks remain pending."
fi
