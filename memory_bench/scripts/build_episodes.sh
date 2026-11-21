#!/usr/bin/env bash
set -euo pipefail

MB_CONFIG="memory_bench/configs/mb001_return_book_to_original_bookcase.yaml"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0
export OMNIGIBSON_GPU_ID=2
export OMNIGIBSON_HEADLESS=1

if (( $# == 0 )); then
    set -- --instance-id 1
fi
python -m memory_bench.episodes.build_episodes "$MB_CONFIG" "$@"
