#!/usr/bin/env bash
source memory_bench/scripts/common.sh
python -m ruff check --config OmniGibson/pyproject.toml memory_bench
python -m ruff format --check --config OmniGibson/pyproject.toml memory_bench
python -m pytest memory_bench/tests -q
