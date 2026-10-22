#!/usr/bin/env bash
set -euo pipefail

MB_CONFIG="memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0
export OMNIGIBSON_GPU_ID=2
export OMNIGIBSON_HEADLESS=1

# Prepare a fresh session in the configured output directory.
python -m memory_bench.collection.prepare_session "$MB_CONFIG" init
python -m memory_bench.collection.prepare_session "$MB_CONFIG" sample --instance-count 5 --seed 42
python -m memory_bench.collection.prepare_session "$MB_CONFIG" assign-variants
# Check raw I metadata and assets, then fix sources before building the corresponding TROs.
python -m memory_bench.collection.prepare_intervent "$MB_CONFIG"
python -m memory_bench.collection.sample_instances "$MB_CONFIG"
