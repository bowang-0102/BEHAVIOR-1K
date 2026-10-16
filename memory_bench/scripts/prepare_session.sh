#!/usr/bin/env bash
source memory_bench/scripts/common.sh

# Edit MODE to run both steps, sampling only, or assembly only.
MODE=all  # all / sample / assemble
TASK_JSON_DIR="$OMNIGIBSON_DATA_PATH/memory-bench-task-instances/scenes/$SCENE_MODEL/json"
SESSION_ROOT="$MB_ROOT/sessions/$MB_SESSION_ID"
POSE_MANIFEST="$SESSION_ROOT/sampled/poses.json"
SCENE_ROOT="$SESSION_ROOT/sampled/scene_snapshots"
INVENTORY="$MB_ROOT/reports/$MB_SESSION_ID/source_inventory.json"

case "$MODE" in
  all|sample|assemble) ;;
  *) echo "Invalid MODE: $MODE. Expected all, sample, or assemble." >&2; exit 1 ;;
esac

# Verify the saved configuration before reusing any session output.
python -m memory_bench.collection.prepare_task "$MB_CONFIG" init

# Generate task instances and their starting poses.
if [[ "$MODE" == all || "$MODE" == sample ]]; then
  if [[ ! -f "$SESSION_ROOT/case_assignments.json" && ! -d "$SESSION_ROOT/tro" ]]; then
    for ACTIVITY in "${ACTIVITIES[@]}"; do
      python OmniGibson/scripts/sampling/sample_b1k_tasks.py --activity "$ACTIVITY" --output_dir "$TASK_JSON_DIR" --overwrite
      # Generate task-state variants only for the first Key activity.
      if [[ "$ACTIVITY" == "${ACTIVITIES[0]}" ]]; then
        python OmniGibson/scripts/sampling/multiply_b1k_tasks.py \
          --activity "$ACTIVITY" --start_idx 1 --end_idx "$INSTANCE_COUNT" \
          --partial_save --headless --output_dir "$TASK_JSON_DIR"
      fi
      mkdir -p "$TASK_JSON_DIR/${SCENE_MODEL}_task_${ACTIVITY}_instances"
      python OmniGibson/scripts/sampling/sample_robot_pose.py --activity "$ACTIVITY" --output_dir "$TASK_JSON_DIR"
    done
  else
    echo "Reusing task sampling results for session $MB_SESSION_ID."
  fi
  python -m memory_bench.collection.prepare_task "$MB_CONFIG" assign-variants
  python -m memory_bench.collection.sample_instances "$MB_CONFIG"
  python -m memory_bench.processing.recordings "$MB_CONFIG"
  echo "Sampling stage complete for session $MB_SESSION_ID: $SESSION_ROOT/sampled"
fi

# Write the instance manifests used for collection.
if [[ "$MODE" == all || "$MODE" == assemble ]]; then
  [[ -f "$POSE_MANIFEST" ]] || { echo "Pose manifest is missing: $POSE_MANIFEST. Run MODE=sample first." >&2; exit 1; }
  [[ -d "$SCENE_ROOT" ]] || { echo "Scene snapshot directory is missing: $SCENE_ROOT. Run MODE=sample first." >&2; exit 1; }
  [[ -f "$INVENTORY" ]] || { echo "Source recording inventory is missing: $INVENTORY. Run MODE=sample first." >&2; exit 1; }
  python -m memory_bench.collection.prepare_task "$MB_CONFIG" assemble-instances \
    --pose-manifest "$POSE_MANIFEST" --scene-root "$SCENE_ROOT" --inventory "$INVENTORY"
  echo "Assembly stage complete for session $MB_SESSION_ID."
fi
