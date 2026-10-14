#!/usr/bin/env bash
source memory_bench/scripts/common.sh

# Edit MODE to run both steps, sampling only, or finalization only.
MODE=all  # all / sample / finalize
TASK_JSON_DIR="$OMNIGIBSON_DATA_PATH/memory-bench-task-instances/scenes/$SCENE_MODEL/json"
SESSION_ROOT="$MB_ROOT/sessions/$MB_SESSION_ID"
POSE_MANIFEST="$SESSION_ROOT/sampled/poses.json"
SCENE_ROOT="$SESSION_ROOT/sampled/scene_snapshots"
INVENTORY="$MB_ROOT/reports/$MB_SESSION_ID/source_inventory.json"

# Generate task instances and their starting poses.
if [[ "$MODE" == all || "$MODE" == sample ]]; then
  if [[ -e "$SESSION_ROOT/case_assignments.json" || -e "$SESSION_ROOT/sampled/poses.json" || -e "$INVENTORY" ]]; then
    echo "Sampling output already exists under $SESSION_ROOT; use a fresh output_root." >&2
    exit 1
  fi
  python -m memory_bench.collection.prepare_task "$MB_CONFIG" init
  for ACTIVITY in "${ACTIVITIES[@]}"; do
    python OmniGibson/scripts/sampling/sample_b1k_tasks.py --activity "$ACTIVITY" --output_dir "$TASK_JSON_DIR" --overwrite
    if [[ "$ACTIVITY" == "${ACTIVITIES[0]}" ]]; then
      python OmniGibson/scripts/sampling/multiply_b1k_tasks.py \
        --activity "$ACTIVITY" --start_idx 1 --end_idx "$INSTANCE_COUNT" \
        --partial_save --headless --output_dir "$TASK_JSON_DIR"
    fi
    mkdir -p "$TASK_JSON_DIR/${SCENE_MODEL}_task_${ACTIVITY}_instances"
    python OmniGibson/scripts/sampling/sample_robot_pose.py --activity "$ACTIVITY" --output_dir "$TASK_JSON_DIR"
  done
  python -m memory_bench.collection.prepare_task "$MB_CONFIG" freeze-cases
  python -m memory_bench.collection.sample_instances "$MB_CONFIG"
  python -m memory_bench.processing.sources "$MB_CONFIG"
  echo "Sampled session $MB_SESSION_ID ($INSTANCE_COUNT instances): $SESSION_ROOT/sampled"
fi

# Write the instance manifests used for collection.
if [[ "$MODE" == all || "$MODE" == finalize ]]; then
  [[ -f "$POSE_MANIFEST" ]] || { echo "Pose manifest not found: $POSE_MANIFEST" >&2; exit 1; }
  [[ -d "$SCENE_ROOT" ]] || { echo "Sampled scene snapshots not found: $SCENE_ROOT" >&2; exit 1; }
  [[ -f "$INVENTORY" ]] || { echo "Source inventory not found: $INVENTORY; run sample first." >&2; exit 1; }
  python -m memory_bench.collection.prepare_task "$MB_CONFIG" finalize \
    --pose-manifest "$POSE_MANIFEST" --scene-root "$SCENE_ROOT" --inventory "$INVENTORY"
  echo "Instance manifests written for $MB_SESSION_ID; unperformed calibration checks remain pending."
fi
