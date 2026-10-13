#!/usr/bin/env bash
# Usage: split_recordings.sh [SESSION_CONFIG] [INSTANCE_ID]
# With no instance ID, process every configured instance.
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
run_python -m memory_bench.processing.split_recordings "$MB_CONFIG" "${@:2}"
