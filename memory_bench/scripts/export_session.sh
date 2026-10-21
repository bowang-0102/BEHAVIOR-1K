#!/usr/bin/env bash
source memory_bench/scripts/common.sh

# Consume Step 3 recipes without planning navigation or converting source files.
python -m memory_bench.episodes.compose_episodes "$MB_CONFIG"
python -m memory_bench.episodes.render_episodes "$MB_CONFIG"
