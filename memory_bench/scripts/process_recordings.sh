#!/usr/bin/env bash
source memory_bench/scripts/common.sh

INVENTORY="reports/$MB_SESSION_ID/source_inventory.json"
# Leave empty to process every instance. Use --resume after an interrupted preparation.
PROCESS_OPTIONS=()
# PROCESS_OPTIONS=(--instance-id 1 --resume)

python -m memory_bench.processing.process_recordings "$MB_CONFIG" --inventory "$INVENTORY" "${PROCESS_OPTIONS[@]}"
