#!/usr/bin/env bash
set -euo pipefail

MB_CONFIG="memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0
export OMNIGIBSON_GPU_ID=0
export OMNIGIBSON_HEADLESS=1

python -m memory_bench.episodes.build_episodes "$MB_CONFIG"
