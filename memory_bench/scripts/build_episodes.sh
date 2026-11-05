#!/usr/bin/env bash
set -euo pipefail

MB_CONFIG="memory_bench/configs/mb000_retrieve_stored_flashlight.yaml"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0
export OMNIGIBSON_GPU_ID=2
export OMNIGIBSON_HEADLESS=1

python -m memory_bench.episodes.build_episodes "$MB_CONFIG" --instance-id 4 --demo-id 3
