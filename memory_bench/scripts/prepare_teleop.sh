#!/usr/bin/env bash
# Prepare the mug_in_top_cabinet Key instances. Args: instance count N (multiple of 3, default 3), seed (default 0).
set -euo pipefail

NUM_INSTANCES="${1:-3}"
SEED="${2:-0}"

export OMNIGIBSON_TASK_INSTANCES_DATASET=memory-bench-task-instances
export PYTHONPATH=memory_bench/src
# 是否需要开窗口检查
export OMNIGIBSON_HEADLESS=1
export PYTHONBREAKPOINT=0

# step1: 初始化：复制必要的脚本到 OMNIGIBSON_TASK_INSTANCES_DATASE
python memory_bench/scripts/prepare_task.py mug_in_top_cabinet init
# step2: 模板采样：采样任务模板json
python OmniGibson/scripts/sampling/sample_b1k_tasks.py --activity mb_mug_into_top_cabinet --overwrite
# step3: 实例采样
python OmniGibson/scripts/sampling/multiply_b1k_tasks.py --activity mb_mug_into_top_cabinet --start_idx 1 --end_idx "$NUM_INSTANCES" --partial_save --headless
# step4: 为每个实例采样机器人初始位姿
python OmniGibson/scripts/sampling/sample_robot_pose.py --activity mb_mug_into_top_cabinet
# step5: 为每个实例构造不同的memory依赖
python memory_bench/scripts/prepare_task.py mug_in_top_cabinet assign-variants --seed "$SEED"
# step6: 注册任务，使joylo可以识别
python memory_bench/scripts/prepare_task.py mug_in_top_cabinet register-joylo
