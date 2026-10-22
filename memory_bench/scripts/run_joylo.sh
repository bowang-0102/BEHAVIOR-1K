#!/usr/bin/env bash
set -euo pipefail

GELLO_PORT="/dev/ttyUSB0"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0

exec python joylo/scripts/run_joylo.py \
  --robot-port 6001 \
  --hostname 127.0.0.1 \
  --hz 100 \
  --gello-model r1pro \
  --gello-name default \
  --gello-port "$GELLO_PORT" \
  --damping-motor-kp 0.0 \
  --use-joycons
