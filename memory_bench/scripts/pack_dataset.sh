#!/usr/bin/env bash
set -euo pipefail

# Run after all eight MB tasks have finished replay, with behavior already activated.
REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
SOURCE_ROOT="$REPO/outputs/memory_bench_1116"
DATASET_ROOT="$REPO/outputs/MemoryBench-Dataset"
MB_TASKS=(
    mb000_retrieve_bottle_of_milk
    mb001_return_book_to_original_bookcase
    mb002_place_reading_lamp_by_previous_state
    mb003_place_radio_by_previous_state
    mb004_put_away_mug_and_tissue_box
    mb005_return_bowl_and_cutting_board
    mb006_place_gaming_controller_on_original_nightstand
    mb007_retrieve_relocated_meat_jar
)

cd "$REPO"
export PYTHONPATH="$PWD"

for task in "${MB_TASKS[@]}"; do
    if [[ ! -f "$SOURCE_ROOT/lerobot_shards/$task/complete.json" ]]; then
        printf 'MB task replay is not complete: %s\n' "$task" >&2
        exit 1
    fi
done

# Append --overwrite only when intentionally rebuilding an existing final dataset.
python -m memory_bench.episodes.pack_dataset "$SOURCE_ROOT" "$DATASET_ROOT"
