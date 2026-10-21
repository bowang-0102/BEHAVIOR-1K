#!/usr/bin/env bash
source memory_bench/scripts/common.sh

ORDER_SEED=1
MATERIAL_GROUPS="sessions/$MB_SESSION_ID/material_groups.json"

# Keep each instance's fixed I sources, assign task order, compose, then render.
python -m memory_bench.episodes.pair_segments "$MB_CONFIG" --order-seed "$ORDER_SEED"
python -m memory_bench.episodes.compose_episodes "$MB_CONFIG" "$MATERIAL_GROUPS"
python -m memory_bench.episodes.render_episodes "$MB_CONFIG"
