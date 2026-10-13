#!/usr/bin/env bash
# Usage: check.sh
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
run_python -m ruff check --config OmniGibson/pyproject.toml memory_bench
run_python -m ruff format --check --config OmniGibson/pyproject.toml memory_bench
run_python -m pytest memory_bench/tests -q
