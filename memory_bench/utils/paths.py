"""Session paths; internal references are relative to the output root."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory_bench.session import Session

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = Path(os.environ.get("OMNIGIBSON_DATA_PATH", REPO_ROOT / "datasets")).resolve()
TASK_INSTANCES_DATASET = "memory-bench-task-instances"
TASK_INSTANCES_PATH = DATA_PATH / TASK_INSTANCES_DATASET
OFFICIAL_TASK_INSTANCES_PATH = DATA_PATH / "2026-challenge-task-instances"


@dataclass(frozen=True)
class SessionPaths:
    data_root: Path
    intervent_rawdata_root: Path
    output_root: Path

    @classmethod
    def from_config(cls, config):
        if any(not Path(config[key]).expanduser().is_absolute() for key in cls.__annotations__):
            raise ValueError("Session root paths must be absolute, independent of the working directory")
        paths = cls(**{key: Path(config[key]).expanduser().resolve() for key in cls.__annotations__})
        configured = os.environ.get("OMNIGIBSON_DATA_PATH")
        if configured and Path(configured).resolve() != paths.data_root:
            raise ValueError("paths.data_root must match OMNIGIBSON_DATA_PATH")
        configured = os.environ.get("MB_ROOT")
        if configured and Path(configured).resolve() != paths.output_root:
            raise ValueError("paths.output_root must match MB_ROOT")
        return paths

    def resolve(self, path):
        path = Path(path)
        return path if path.is_absolute() else self.output_root / path

    def relative(self, path):
        path = Path(path).resolve()
        return str(path.relative_to(self.output_root)) if path.is_relative_to(self.output_root) else str(path)

    def activate(self):
        os.environ["OMNIGIBSON_DATA_PATH"] = str(self.data_root)
        os.environ["OMNIGIBSON_TASK_INSTANCES_DATASET"] = TASK_INSTANCES_DATASET
        os.environ["MB_ROOT"] = str(self.output_root)


def _task_instance_root(session: "Session") -> Path:
    return session.paths.data_root / TASK_INSTANCES_DATASET


def _scene_json_dir(session: "Session", task_instance_root: Path) -> Path:
    return task_instance_root / "scenes" / session.task.scene_model / "json"
