#!/usr/bin/env bash
set -euo pipefail

MB_CONFIG="memory_bench/configs/mb003_place_radio_by_previous_state.yaml"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0
export OMNIGIBSON_GPU_ID=3
export OMNIGIBSON_HEADLESS=1

COMPOSE_ARGS=(--instance-id 1)
if (( $# )); then
    COMPOSE_ARGS=("$@")
fi
python -m memory_bench.episodes.compose_episodes "$MB_CONFIG" "${COMPOSE_ARGS[@]}"
python -m memory_bench.episodes.replay_episodes "$MB_CONFIG" --rgb "$@"
