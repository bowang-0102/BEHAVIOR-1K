"""Frozen session configuration and ordering constraints."""

from dataclasses import dataclass
from pathlib import Path

import yaml

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
    def segments(self):
        return self.config["segments"]

    @property
    def key_ids(self):
        return self.config["collection"]["key_segment_ids"]

    @property
    def query_id(self):
        queries = [key for key, item in self.segments.items() if item["role"] == "query_task"]
        if len(queries) != 1:
            raise ValueError("A session must contain exactly one Query")
        return queries[0]

    @property
    def task(self):
        from memory_bench.tasks import get_task

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
        query = self.segments[self.query_id]
        if self.session_id != query["activity"]:
            raise ValueError("session_id must be the Query BDDL activity")
        key_ids = {key for key, item in self.segments.items() if item["role"] == "key_task"}
        if len(self.key_ids) != len(key_ids) or set(self.key_ids) != key_ids:
            raise ValueError("collection.key_segment_ids must contain each Key exactly once")
        dependencies = query["depends_on"]
        if len(set(dependencies)) != len(dependencies) or set(dependencies) != key_ids:
            raise ValueError("Query must declare all of its memory Keys")
        level = 1 if len(key_ids) == 1 else 2
        if not key_ids or self.config["benchmark"]["level"] != level:
            raise ValueError("benchmark.level must match the number of required Keys")
        orders = self.config["composition"]["task_orders"]
        if not 1 <= len(orders) <= 3:
            raise ValueError("Provide one to three candidate task orders")
        for order in orders:
            self.validate_order(order)
        for i, key in enumerate(self.key_ids):
            if not set(self.segments[key]["depends_on"]).issubset(self.key_ids[:i]):
                raise ValueError(f"Collection order violates dependencies of {key}")
        memory_room = self.config["memory_room"]
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
            if not set(item["depends_on"]).issubset(self.segments):
                raise ValueError(f"Unknown dependency for {key}")
        if self.config["instance_sampling"]["count"] <= 0:
            raise ValueError("instance_sampling.count must be positive")
