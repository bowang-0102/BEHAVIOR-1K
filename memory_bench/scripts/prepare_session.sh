#!/usr/bin/env bash
source memory_bench/scripts/common.sh

TASK_JSON_DIR="$OMNIGIBSON_DATA_PATH/memory-bench-task-instances/scenes/$SCENE_MODEL/json"
SESSION_ROOT="$MB_ROOT/sessions/$MB_SESSION_ID"

# Verify the saved configuration before reusing any session output.
python -m memory_bench.collection.prepare_session "$MB_CONFIG" init

# Original sampler files are inputs; final session TROs are written only after simulation checks.
if [[ ! -f "$SESSION_ROOT/case_assignments.json" ]]; then
  for ACTIVITY in "${ACTIVITIES[@]}"; do
    python OmniGibson/scripts/sampling/sample_b1k_tasks.py --activity "$ACTIVITY" --output_dir "$TASK_JSON_DIR" --overwrite
    if [[ "$ACTIVITY" == "${ACTIVITIES[0]}" ]]; then
      python OmniGibson/scripts/sampling/multiply_b1k_tasks.py \
        --activity "$ACTIVITY" --start_idx 1 --end_idx "$INSTANCE_COUNT" \
        --partial_save --headless --output_dir "$TASK_JSON_DIR"
      # All Key/Query segments reuse this first Key's robot start.
      mkdir -p "$TASK_JSON_DIR/${SCENE_MODEL}_task_${ACTIVITY}_instances"
      python OmniGibson/scripts/sampling/sample_robot_pose.py --activity "$ACTIVITY" --output_dir "$TASK_JSON_DIR"
    fi
  done
else
  echo "Reusing task sampling inputs for session $MB_SESSION_ID."
fi

python -m memory_bench.collection.prepare_session "$MB_CONFIG" assign-variants
# Scan, prepare and freeze each instance's I recordings before building its TRO.
python -m memory_bench.processing.prepare_intervent "$MB_CONFIG" --resume
python -m memory_bench.collection.sample_instances "$MB_CONFIG"
echo "Preparation complete for session $MB_SESSION_ID: $SESSION_ROOT"
