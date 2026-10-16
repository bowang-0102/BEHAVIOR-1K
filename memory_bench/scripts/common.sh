#!/usr/bin/env bash
# Edit these parameters before running the scripts.
set -euo pipefail

REPO_ROOT="/data/bwang/code/BEHAVIOR-1K"
MB_CONFIG="$REPO_ROOT/memory_bench/configs/mb_retrieve_mug_from_top_cabinet.yaml"
OMNIGIBSON_DATA_PATH="$REPO_ROOT/datasets"
MB_ROOT="/data/bwang/datasets/memory-bench"
MB_SESSION_ID="mb_retrieve_mug_from_top_cabinet"
SCENE_MODEL="house_single_floor"
INSTANCE_COUNT=5 # for test
# Key activities first, then Query. These values must match MB_CONFIG.
ACTIVITIES=(
  "mb_mug_into_top_cabinet"
  "mb_retrieve_mug_from_top_cabinet"
)

cd "$REPO_ROOT"
export OMNIGIBSON_DATA_PATH MB_ROOT
export OMNIGIBSON_TASK_INSTANCES_DATASET=memory-bench-task-instances
export OMNIGIBSON_HEADLESS=1
export PYTHONBREAKPOINT=0
export PYTHONPATH="$REPO_ROOT"
