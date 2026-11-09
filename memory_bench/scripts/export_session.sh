#!/usr/bin/env bash
set -euo pipefail

MB_CONFIG="memory_bench/configs/mb_retrieve_baking_soda_from_top_cabinet.yaml"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0
export OMNIGIBSON_GPU_ID=2
export OMNIGIBSON_HEADLESS=1

python -m memory_bench.episodes.compose_episodes "$MB_CONFIG" --instance-id 4
# python -m memory_bench.episodes.replay_episodes "$MB_CONFIG" --rgb
