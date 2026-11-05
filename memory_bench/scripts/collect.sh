#!/usr/bin/env bash
set -euo pipefail

MB_CONFIG="memory_bench/configs/mb000_retrieve_stored_flashlight.yaml"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0
export OMNIGIBSON_GPU_ID=0
export DISPLAY=:0
export XAUTHORITY="$HOME/.Xauthority"
export OMNIGIBSON_HEADLESS=0
unset OMNIGIBSON_REMOTE_STREAMING

exec python joylo/scripts/launch_og.py --session-config "$MB_CONFIG"
