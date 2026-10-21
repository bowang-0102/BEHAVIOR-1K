#!/usr/bin/env bash
source memory_bench/scripts/common.sh

DEMO_ID=1
ORDER_SEED=1
BUILD_OPTIONS=()
# BUILD_OPTIONS=(--instance-id 1)

python -m memory_bench.episodes.build_bridges "$MB_CONFIG" --demo-id "$DEMO_ID" --order-seed "$ORDER_SEED" "${BUILD_OPTIONS[@]}"
