#!/usr/bin/env bash
source memory_bench/scripts/common.sh

# Leave empty to process every instance. Use --resume after an interrupted preparation.
PROCESS_OPTIONS=()
# PROCESS_OPTIONS=(--instance-id 1 --resume)

# I sources were prepared and assigned before collection; this step processes only K/Q.
python -m memory_bench.processing.process_recordings "$MB_CONFIG" "${PROCESS_OPTIONS[@]}"
