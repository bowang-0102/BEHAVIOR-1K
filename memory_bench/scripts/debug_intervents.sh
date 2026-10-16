#!/usr/bin/env bash
set -euo pipefail

# Activate the preinstalled behavior environment before running this script.
REPO_ROOT="/data/bwang/code/BEHAVIOR-1K"
MB_DEBUG_CONFIG="$REPO_ROOT/memory_bench/configs/debug_intervents.yaml"
STAGE="all" # all, prepare, scene, audit, bridge, compose, render, or readback
GPU_ID=0
RGB_ONLY=true # First inspect RGB; action, instruction and proprio metadata are retained.

cd "$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT"
export OMNIGIBSON_HEADLESS=1
export PYTHONBREAKPOINT=0
export CUDA_VISIBLE_DEVICES="$GPU_ID"

if [[ "$STAGE" == "all" ]]; then
    for stage in prepare scene audit bridge compose render readback; do
        if [[ "$stage" == "render" && "$RGB_ONLY" == true ]]; then
            python -u -m memory_bench.processing.debug_intervents "$MB_DEBUG_CONFIG" render --rgb-only
        else
            python -u -m memory_bench.processing.debug_intervents "$MB_DEBUG_CONFIG" "$stage"
        fi
    done
else
    if [[ "$STAGE" == "render" && "$RGB_ONLY" == true ]]; then
        python -u -m memory_bench.processing.debug_intervents "$MB_DEBUG_CONFIG" render --rgb-only
    else
        python -u -m memory_bench.processing.debug_intervents "$MB_DEBUG_CONFIG" "$STAGE"
    fi
fi
