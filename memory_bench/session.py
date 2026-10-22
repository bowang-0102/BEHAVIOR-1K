"""Single configuration entry point for benchmark sessions.

Load session YAML once with Session.load(), then use the configuration-group properties.
The original config mapping is retained unchanged for frozen session snapshots.
"""

from dataclasses import dataclass
import json
from pathlib import Path

import yaml

from memory_bench.tasks import get_task
from memory_bench.utils.paths import SessionPaths


@dataclass
class Session:
    config: dict
    paths: SessionPaths

    @classmethod
    def load(cls, path):
        config = yaml.safe_load(Path(path).read_text())
        session = cls(config, SessionPaths.from_config(config["paths"]))
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
    def intervent_selection(self):
        return self.config["intervent_selection"]

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
        assignments_path = self.paths.resolve(f"sessions/{self.session_id}/variant_assignments.json")
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
        if self.action_frequency <= 0:
            raise ValueError("bridge.action_frequency must be positive")

        query = self.segments[self.query_id]
        if self.session_id != query["activity"]:
            raise ValueError("session_id must be the Query BDDL activity")
        key_ids = set(self.key_ids)
        dependencies = query["depends_on"]
        if set(dependencies) != key_ids:
            raise ValueError("Query must declare all of its memory Keys")
        level = 1 if len(key_ids) == 1 else 2
        if not key_ids or self.benchmark["level"] != level:
            raise ValueError("benchmark.level must match the number of required Keys")
        orders = self.composition["task_orders"]
        if not 1 <= len(orders) <= 3:
            raise ValueError("Provide one to three candidate task orders")
        for order in orders:
            self.validate_order(order)
        for i, key in enumerate(self.key_ids):
            if not set(self.segments[key]["depends_on"]).issubset(self.key_ids[:i]):
                raise ValueError(f"Collection order violates dependencies of {key}")
        memory_room = self.memory_room
        if any(self.segments[key]["room_id"] != memory_room for key in [*self.key_ids, self.query_id]):
            raise ValueError("All Keys and Query must share the memory room instance")
        rooms = [item["room_id"] for item in self.segments.values() if item["role"] == "intervent_task"]
        if memory_room in rooms or len(rooms) != len(set(rooms)):
            raise ValueError("Intervening tasks need distinct room instances outside the memory room")
        roles = {"key_task", "query_task", "intervent_task"}
        for key, item in self.segments.items():
            if item["role"] not in roles:
                raise ValueError(f"Unexpected task role for {key}")
            if len(item["depends_on"]) != len(set(item["depends_on"])):
                raise ValueError(f"Repeated dependencies for {key}")
        for key, requirement in self.assets.get("requirements", {}).items():
            if requirement["source"] not in {"scene", "insert", "scene_or_insert"}:
                raise ValueError(f"Unknown asset source in assets.requirements.{key}: {requirement['source']}")
