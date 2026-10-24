from dataclasses import dataclass
from pathlib import Path


@dataclass
class SessionPaths:
    data_root: Path
    intervent_rawdata_root: Path
    output_root: Path
    session_id: str
    scene_model: str

    def __post_init__(self):
        roots = [Path(root).expanduser() for root in (self.data_root, self.intervent_rawdata_root, self.output_root)]
        if any(not root.is_absolute() for root in roots):
            raise ValueError("Session root paths must be absolute, independent of the working directory")
        self.data_root, self.intervent_rawdata_root, self.output_root = [root.resolve() for root in roots]
        self.task_instances_dir = self.output_root / "memory-bench-task-instances"
        self.scene_json_dir = self.task_instances_dir / "scenes" / self.scene_model / "json"
        self.session_dir = self.output_root / "sessions" / self.session_id
        self.recordings_dir = self.output_root / "recordings" / self.session_id
        self.recipes_dir = self.output_root / "recipes" / self.session_id
        self.bridges_dir = self.output_root / "bridges" / self.session_id
        self.episodes_dir = self.output_root / "episodes" / self.session_id
        self.export_dir = self.output_root / "lerobot" / self.session_id
        self.reports_dir = self.output_root / "reports" / self.session_id

    def resolve(self, path):
        """Resolve an output-relative reference or an absolute external input."""
        return self.output_root / path
