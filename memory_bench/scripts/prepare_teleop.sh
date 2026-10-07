#!/usr/bin/env bash
# Prepare the mug_in_top_cabinet Key instances. Args: instance count N (multiple of 3, default 3), seed (default 0).
set -euo pipefail

NUM_INSTANCES="${1:-3}"
SEED="${2:-0}"

export OMNIGIBSON_TASK_INSTANCES_DATASET=memory-bench-task-instances
export OMNIGIBSON_HEADLESS=1
export PYTHONPATH=memory_bench/src
export PYTHONBREAKPOINT=0

python memory_bench/scripts/prepare_task.py mug_in_top_cabinet init
python OmniGibson/scripts/sampling/sample_b1k_tasks.py --activity mb_mug_into_top_cabinet --overwrite
python OmniGibson/scripts/sampling/multiply_b1k_tasks.py --activity mb_mug_into_top_cabinet --start_idx 1 --end_idx "$NUM_INSTANCES" --partial_save --headless
python OmniGibson/scripts/sampling/sample_robot_pose.py --activity mb_mug_into_top_cabinet
python memory_bench/scripts/prepare_task.py mug_in_top_cabinet assign-variants --seed "$SEED"
python memory_bench/scripts/prepare_task.py mug_in_top_cabinet register-joylo
