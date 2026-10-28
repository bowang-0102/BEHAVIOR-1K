#!/usr/bin/env bash
set -euo pipefail

MB_CONFIG="memory_bench/configs/mb_retrieve_baking_soda_from_top_cabinet.yaml"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0
export OMNIGIBSON_GPU_ID=2
export OMNIGIBSON_HEADLESS=1

echo "[1/5] Initialize session"
python -m memory_bench.collection.prepare_session "$MB_CONFIG" init

echo "[2/5] Sample tasks"
python -m memory_bench.collection.prepare_session "$MB_CONFIG" sample --instance-count 5 --seed 42

echo "[3/5] Assign variants"
python -m memory_bench.collection.prepare_session "$MB_CONFIG" assign-variants

echo "[4/5] Prepare interventions"
python -m memory_bench.collection.prepare_intervent "$MB_CONFIG"

echo "[5/5] Generate final instances"
python -m memory_bench.collection.sample_instances "$MB_CONFIG"

echo "[DONE] All preparation steps complete"
