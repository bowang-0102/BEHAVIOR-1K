import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_PATH = Path(os.environ.get("OMNIGIBSON_DATA_PATH", REPO_ROOT / "datasets"))

# Upstream tools use this dataset when OMNIGIBSON_TASK_INSTANCES_DATASET is set to its name.
TASK_INSTANCES_DATASET = "memory-bench-task-instances"
TASK_INSTANCES_PATH = DATA_PATH / TASK_INSTANCES_DATASET
OFFICIAL_TASK_INSTANCES_PATH = DATA_PATH / "2026-challenge-task-instances"
