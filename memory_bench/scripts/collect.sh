#!/usr/bin/env bash
source memory_bench/scripts/common.sh

INSTANCE_ID=1
export DISPLAY=:0
export XAUTHORITY="$HOME/.Xauthority"
export OMNIGIBSON_HEADLESS=0
unset OMNIGIBSON_REMOTE_STREAMING

# Add --session-recording-dir "/path/to/instance_0001" here for a custom recording directory.
exec python joylo/scripts/launch_og.py \
  --session-config "$MB_CONFIG" --session-instance-id "$INSTANCE_ID"
