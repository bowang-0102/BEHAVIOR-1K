"""Read HDF5 recording metadata and frame states, and scan compatible intervention recordings.

Run this module with a session config to write reports/<session_id>/source_inventory.json.
"""

import argparse
import json
from pathlib import Path

import h5py

from memory_bench.session import Session
from memory_bench.utils.state import read_json, write_json


def recording_metadata(path, demo=0):
    with h5py.File(path, "r") as stream:
        data = stream["data"]
        config = json.loads(data.attrs["config"])
        scene = json.loads(data.attrs["scene_file"])
        trajectory = data[f"demo_{demo}"]
        robot = config["robots"][0]
        if len(config["robots"]) != 1:
            raise ValueError("Memory Bench currently requires one robot")
        for name in ("type", "controller_config", "action_normalize"):
            if name not in robot:
                raise ValueError(f"Missing robot interface field: {name}")
        if config["scene"]["scene_model"] is None:
            raise ValueError("Missing scene model")
        model = robot["model"] if robot["type"] == "Robot" else robot["type"]
        # The 2026 recordings predate the generic Robot and batched controller schema.
        initializer = scene["objects_info"]["init_info"][robot["name"]]
        if initializer["class_module"] == "omnigibson.robots.r1pro":
            initializer.update(class_module="omnigibson.robots.robot", class_name="Robot")
            initializer["args"]["model"] = "r1pro"
        robot_state = scene["state"]["registry"]["object_registry"][robot["name"]]
        if "controllers" in robot_state:
            robot_state["controller_groups"] = {
                name: {
                    **{key: value for key, value in state.items() if key not in ("goal_is_valid", "goal")},
                    "goal_set": state["goal_is_valid"],
                    "goals": state["goal"],
                }
                for name, state in robot_state.pop("controllers").items()
            }
        interface = {
            "robot_type": model.lower(),
            "robot_name": robot["name"],
            "controller_config": robot["controller_config"],
            "action_normalize": robot["action_normalize"],
            "action_dim": trajectory["action"].shape[1],
            "action_frequency": config["env"]["action_frequency"],
            "fps": config["env"]["rendering_frequency"],
            "physics_frequency": config["env"]["physics_frequency"],
        }
        if interface["action_frequency"] != interface["fps"]:
            raise ValueError("One action per rendered frame is required")
        transitions = json.loads(trajectory.attrs.get("transitions", "{}"))
        if any(transitions.values()):
            raise ValueError("Runtime object/system changes are not supported in the first pipeline")
        if "init_metadata" in trajectory and len(trajectory["init_metadata"]):
            raise ValueError("Per-demo object attribute overrides need an explicit asset merge contract")
        if scene["state"]["registry"]["system_registry"]:
            raise ValueError("Particle systems require a separate task-specific merge contract")
        if trajectory["action"].shape[0] == 0:
            raise ValueError("Recording contains no actions")
        return {
            "config": config,
            "scene": scene,
            "interface": interface,
            "frames": trajectory["action"].shape[0],
            "states": trajectory["state_json"].shape[0] if "state_json" in trajectory else trajectory["state"].shape[0],
            "activity": config["task"].get("activity_name"),
        }


def task_assets(scene, robot_name, extra=()):
    bindings = scene["metadata"]["task"]["inst_to_name"]
    public = {name for key, name in bindings.items() if key.startswith(("agent.", "floor."))}
    names = sorted((set(bindings.values()) | set(extra)) - public - {robot_name})
    return names, bindings


def inspect_source(path, segment, expected_scene, extra=(), demo=0):
    metadata = recording_metadata(path, demo)
    if metadata["activity"] != segment["activity"] or metadata["config"]["scene"]["scene_model"] != expected_scene:
        raise ValueError("Source activity or scene does not match session")
    names, bindings = task_assets(metadata["scene"], metadata["interface"]["robot_name"], extra)
    init = metadata["scene"]["objects_info"]["init_info"]
    rooms = set()
    for name in names:
        declared = init[name]["args"]["in_rooms"]
        rooms.update([declared] if isinstance(declared, str) else declared)
    if rooms != {segment["room_id"]}:
        raise ValueError(f"Actual task rooms {sorted(rooms)} do not match {segment['room_id']}")
    return {
        "hdf5": str(Path(path).resolve()),
        "demo": demo,
        "frames": [0, metadata["frames"]],
        "interface": metadata["interface"],
        "used": names,
        "bindings": bindings,
        "has_terminal_state": metadata["states"] > metadata["frames"],
    }


def scan_sources(session):
    inventory_path = session.paths.output_root / "reports" / session.session_id / "source_inventory.json"
    if inventory_path.exists():
        report = read_json(inventory_path)
        if report["session_id"] != session.session_id:
            raise ValueError(f"Source recording inventory belongs to a different session: {inventory_path}")
        print(f"Reusing source recording inventory: {inventory_path}")
        return report

    report = {"session_id": session.session_id, "tasks": {}}
    for key, segment in session.segments.items():
        if segment["role"] != "intervent_task":
            continue
        root = session.paths.intervent_rawdata_root / f"task-{segment['source']['task_id']:04d}"
        candidates, rejected = [], []
        for path in sorted(root.glob("episode_*.hdf5")):
            try:
                with h5py.File(path, "r") as stream:
                    demos = [
                        int(name.removeprefix("demo_"))
                        for name, group in stream["data"].items()
                        if name.startswith("demo_") and "action" in group and len(group["action"]) > 0
                    ]
                if not demos:
                    raise ValueError("No demo group contains actions")
                candidates.append(
                    inspect_source(
                        path, segment, session.config["scene_model"], segment.get("extra_objects", []), max(demos)
                    )
                )
            except (ValueError, KeyError, OSError) as error:
                rejected.append({"hdf5": str(path), "reason": str(error)})
        expected = session.config["intervent_selection"]["candidate_count_per_task"]
        report["tasks"][key] = {
            "available": len(candidates) + len(rejected),
            "compatible_metadata": len(candidates),
            "expected_candidates": expected,
            "candidate_shortfall": max(0, expected - len(candidates) - len(rejected)),
            "candidates": candidates,
            "rejected": rejected,
        }
    write_json(inventory_path, report)
    print(f"Source recording inventory saved: {inventory_path}")
    return report


class FrameReader:
    """Canonical state_json stores pre-action scene state and one post-action terminal state."""

    def __init__(self, path, demo=0):
        self.stream = h5py.File(path, "r")
        self.group = self.stream["data"][f"demo_{demo}"]
        if "state_json" not in self.group:
            self.stream.close()
            raise ValueError(f"Normalize serialized source first: {path}")
        if len(self.group["state_json"]) != len(self.group["action"]) + 1:
            self.stream.close()
            raise ValueError("Canonical trajectories must include the post-action terminal state")

    def state(self, frame):
        return json.loads(self.group["state_json"][frame])

    def close(self):
        self.stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    args = parser.parse_args()
    report = scan_sources(Session.load(args.config))
    print(
        json.dumps(
            {
                key: {field: task[field] for field in ("available", "compatible_metadata", "candidate_shortfall")}
                for key, task in report["tasks"].items()
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
