#!/usr/bin/env bash
set -euo pipefail

MB_CONFIG="memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0
export OMNIGIBSON_GPU_ID=0
export DISPLAY=:0
export XAUTHORITY="$HOME/.Xauthority"
export OMNIGIBSON_HEADLESS=0
unset OMNIGIBSON_REMOTE_STREAMING

exec python joylo/scripts/launch_og.py --session-config "$MB_CONFIG"
