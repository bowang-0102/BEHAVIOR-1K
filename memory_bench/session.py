import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from memory_bench.tasks import get_task


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


@dataclass
class Session:
    config: dict
    paths: SessionPaths

    @classmethod
    def load(cls, path):
        config = yaml.safe_load(Path(path).read_text())
        paths = SessionPaths(**config["paths"], session_id=config["session_id"], scene_model=config["scene_model"])
        session = cls(config, paths)
        session.validate()
        return session

    @property
    def session_id(self):
        return self.config["session_id"]

    @property
    def scene_model(self):
        return self.config["scene_model"]

    @property
    def memory_room(self):
        return self.config["memory_room"]

    @property
    def benchmark(self):
        return self.config["benchmark"]

    @property
    def instance_sampling(self):
        return self.config["instance_sampling"]

    @property
    def variant_sampling(self):
        return self.config.get("variant_sampling", {})

    @property
    def assets(self):
        return self.config["assets"]

    @property
    def composition(self):
        return self.config["composition"]

    @property
    def bridge(self):
        return self.config.get("bridge", {})

    @property
    def action_frequency(self):
        """Use the same action rate for collection and synthesized bridges."""
        return int(self.bridge.get("action_frequency", 30))

    @property
    def export(self):
        return self.config["export"]

    @property
    def instance_count(self):
        """Use the actual variant assignments as the downstream instance count."""
        assignments_path = self.paths.session_dir / "variant_assignments.json"
        assignments = json.loads(assignments_path.read_text())
        return len(assignments["instances"])

    @property
    def segments(self):
        return self.config["segments"]

    @property
    def key_ids(self):
        return sorted(
            (key for key, item in self.segments.items() if item["role"] == "key_task"),
            key=lambda key: int(key.removeprefix("key_")),
        )

    @property
    def query_id(self):
        queries = [key for key, item in self.segments.items() if item["role"] == "query_task"]
        if len(queries) != 1:
            raise ValueError("A session must contain exactly one Query")
        return queries[0]

    @property
    def task(self):
        return get_task(self.session_id)

    def validate_order(self, order):
        if len(order) != len(self.segments) or set(order) != set(self.segments):
            raise ValueError("task_order must contain each task exactly once")
        if order[-1] != self.query_id:
            raise ValueError("Query must be the final task")
        positions = {key: i for i, key in enumerate(order)}
        for key, item in self.segments.items():
            for dependency in item["depends_on"]:
                if dependency not in positions or positions[dependency] >= positions[key]:
                    raise ValueError(f"Unmet dependency {dependency} -> {key}")

    def validate(self):
        """Check workflow constraints; consumers read their own configuration fields."""
        query_id = self.query_id
        key_ids = self.key_ids
        query = self.segments[query_id]

        # Task identity and memory dependencies.
        if query["activity"] != self.task.query_activity:
            raise ValueError("Query activity must match the registered task")
        if set(query["depends_on"]) != set(key_ids):
            raise ValueError("Query must declare all of its memory Keys")

        # TODO: Support Level 3.
        level = 1 if len(key_ids) == 1 else 2
        if not key_ids or self.benchmark["level"] != level:
            raise ValueError("benchmark.level must match the number of required Keys")

        # Episode and collection order.
        for order in self.composition["task_orders"]:
            self.validate_order(order)
        for i, key in enumerate(key_ids):
            if not set(self.segments[key]["depends_on"]).issubset(key_ids[:i]):
                raise ValueError(f"Collection order violates dependencies of {key}")

        # Memory and intervening tasks use separate room instances.
        memory_room = self.memory_room
        if any(self.segments[key]["room_id"] != memory_room for key in [*key_ids, query_id]):
            raise ValueError("All Keys and Query must share the memory room instance")
        rooms = [item["room_id"] for item in self.segments.values() if item["role"] == "intervent_task"]
        if memory_room in rooms or len(rooms) != len(set(rooms)):
            raise ValueError("Intervening tasks need distinct room instances outside the memory room")
