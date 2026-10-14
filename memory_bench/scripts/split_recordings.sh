#!/usr/bin/env bash
source memory_bench/scripts/common.sh

INSTANCE_ID=""  # Empty means all instances; set to 1, 2, ... for a single instance.

if [[ -n "$INSTANCE_ID" ]]; then
  python -m memory_bench.processing.split_recordings "$MB_CONFIG" "$INSTANCE_ID"
else
  python -m memory_bench.processing.split_recordings "$MB_CONFIG"
fi
