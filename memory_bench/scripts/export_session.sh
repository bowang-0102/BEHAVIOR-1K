#!/usr/bin/env bash
# Usage: export_session.sh [SESSION_CONFIG] [ORDER_SEED=1]
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

run_python -m memory_bench.processing.prepare_intervents "$MB_CONFIG" "reports/$MB_SESSION_ID/source_inventory.json"
run_python -m memory_bench.processing.pair_sources "$MB_CONFIG" --order-seed "${2:-1}"
run_python -m memory_bench.processing.compose_episodes "$MB_CONFIG" "sessions/$MB_SESSION_ID/material_groups.json"
run_python -m memory_bench.processing.render_episodes "$MB_CONFIG"
