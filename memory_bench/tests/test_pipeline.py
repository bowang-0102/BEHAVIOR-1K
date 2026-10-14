"""Offline regression checks for boundaries, memory inheritance and dependency ordering."""

import json
from copy import deepcopy
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from memory_bench.processing.compose_episodes import compose_episode, mapped_snapshot
from memory_bench.processing.make_segments import make_segment
from memory_bench.processing.recordings import FrameReader, recording_metadata
from memory_bench.processing.render_episodes import validate_annotations
from memory_bench.session import Session
from memory_bench.utils.paths import SessionPaths
from memory_bench.utils.state import file_hash, read_json, require_robot_match, scene_objects, write_json


def snapshot(positions):
    objects = {
        name: {"root_link": {"pos": [value, 0, 0], "ori": [0, 0, 0, 1]}, "non_kin": {}}
        for name, value in positions.items()
    }
    return {
        "robot": {
            "name": "robot",
            "position": [positions["robot"], 0, 0],
            "orientation": [0, 0, 0, 1],
            "joint_positions": [],
            "controlled_joint_indices": [],
        },
        "state": {
            "pos": [0, 0, 0],
            "ori": [0, 0, 0, 1],
            "registry": {"object_registry": objects, "system_registry": {}},
        },
        "objects_info": {
            "init_info": {
                name: {
                    "class_module": "omnigibson.robots.robot"
                    if name == "robot"
                    else "omnigibson.objects.dataset_object",
                    "class_name": "Robot" if name == "robot" else "DatasetObject",
                    "args": {
                        "name": name,
                        "category": name,
                        "model": "test",
                        "scale": [1, 1, 1],
                        "fixed_base": False,
                        "in_rooms": ["kitchen_0"],
                    },
                }
                for name in positions
            }
        },
        "metadata": {"task": {"inst_to_name": {}}},
    }


def canonical(path, entry, exit_state, activity, terminal=True):
    config = {
        "scene": {"scene_model": "house_single_floor"},
        "env": {"action_frequency": 30, "rendering_frequency": 30, "physics_frequency": 120},
        "robots": [
            {
                "type": "R1Pro",
                "name": "robot",
                "controller_config": {"base": {"name": "test"}},
                "action_normalize": False,
            }
        ],
        "task": {"activity_name": activity},
    }
    interface = {
        "robot_type": "r1pro",
        "robot_name": "robot",
        "controller_config": config["robots"][0]["controller_config"],
        "action_normalize": False,
        "action_dim": 23,
        "action_frequency": 30,
        "fps": 30,
        "physics_frequency": 120,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "x") as stream:
        data = stream.create_group("data")
        data.attrs["config"] = json.dumps(config)
        data.attrs["scene_file"] = json.dumps(entry)
        group = data.create_group("demo_0")
        group.attrs["transitions"] = "{}"
        group.attrs["num_samples"] = 2
        group.create_dataset("action", data=np.full((2, 23), 0.25, dtype=np.float32))
        for field in ("reward", "terminated", "truncated"):
            group.create_dataset(field, data=np.zeros(2))
        states = [entry["state"], entry["state"], exit_state["state"]] if terminal else [entry["state"], entry["state"]]
        group.create_dataset("state_json", data=[json.dumps(state) for state in states], dtype=h5py.string_dtype())
    return interface


@pytest.fixture
def material(tmp_path, monkeypatch):
    query = "mb_retrieve_mug_from_top_cabinet"
    definitions = {
        "key_1": {"role": "key_task", "activity": "mb_mug_into_top_cabinet", "room_id": "kitchen_0", "depends_on": []},
        "intervent_1": {
            "role": "intervent_task",
            "activity": "tidying_bedroom",
            "room_id": "bedroom_0",
            "depends_on": [],
        },
        "intervent_2": {
            "role": "intervent_task",
            "activity": "outfit_a_basic_toolbox",
            "room_id": "utility_room_0",
            "depends_on": [],
        },
        "query": {"role": "query_task", "activity": query, "room_id": "kitchen_0", "depends_on": ["key_1"]},
    }
    config = {
        "session_id": query,
        "benchmark": {"level": 1},
        "memory_room": "kitchen_0",
        "scene_model": "house_single_floor",
        "instance_sampling": {"count": 1},
        "segments": definitions,
        "collection": {"key_segment_ids": ["key_1"]},
        "composition": {"task_orders": [["intervent_1", "key_1", "intervent_2", "query"]]},
        "assets": {"manifest": "assets.json"},
        "validation": {"state_tolerances": {"ignored_fields": ["is_asleep"], "numeric": {"default": 1e-6}}},
    }
    session = Session(config, SessionPaths(tmp_path, tmp_path, tmp_path))
    session.validate()
    monkeypatch.setattr("memory_bench.tasks.get_task", lambda _: SimpleNamespace(key_information={"key_1": "location"}))
    positions = {"robot": 0, "mug": 0, "cabinet": 3, "island": 0, "toy": 0, "hammer": 0}
    scene = snapshot(positions)
    write_json(tmp_path / "scene.json", scene)
    write_json(
        tmp_path / "assets.json",
        {
            "protected": ["mug", "cabinet", "island"],
            "intervent_objects": {"intervent_1": ["toy"], "intervent_2": ["hammer"]},
        },
    )
    write_json(
        tmp_path / "instance.json",
        {
            "instance_id": 1,
            "case_id": "A",
            "scene_sha256": file_hash(tmp_path / "scene.json"),
            "bindings": {"key_1": {"mug.n.04_1": "mug"}, "query": {"mug.n.04_1": "mug"}},
        },
    )
    refs = {}
    entries = {}
    for index, (key, write, value) in enumerate(
        (("key_1", "mug", 3), ("intervent_1", "toy", 1), ("intervent_2", "hammer", 2), ("query", "mug", 0))
    ):
        entry_positions = {**positions, "robot": index + 1}
        if key == "query":
            entry_positions["mug"] = 3
        entry = snapshot(entry_positions)
        exit_state = snapshot({**entry_positions, write: value})
        if key.startswith("intervent"):
            entry["metadata"]["task"]["inst_to_name"] = {f"{write}.n.01_1": write}
            entry["objects_info"]["init_info"][write]["args"]["in_rooms"] = [definitions[key]["room_id"]]
        path = tmp_path / f"{key}.hdf5"
        interface = canonical(path, entry, exit_state, definitions[key]["activity"])
        write_json(tmp_path / f"{key}_entry.json", entry)
        write_json(tmp_path / f"{key}_exit.json", exit_state)
        used = [write] if key.startswith("intervent") else ["mug", "cabinet", "island"]
        manifest = {
            "identity": {"id": key, "instance_id": 1},
            "task": {**definitions[key], "instruction": "Retrieve the mug." if key == "query" else key},
            "source": {"hdf5": path.name, "demo": 0, "frames": [0, 2], "sha256": file_hash(path), "fps": 30},
            "states": {"directory": ".", "entry": f"{key}_entry.json", "exit": f"{key}_exit.json"},
            "objects": {
                "used": used,
                "read": used,
                "write": [write],
                "memory_bindings": {"mug.n.04_1": "mug"} if not key.startswith("intervent") else {},
            },
            "interface": interface,
            "validation": {"entry": True, "exit": True, "empty_hands": True, "stable": True},
        }
        write_json(tmp_path / f"{key}.json", manifest)
        refs[key] = f"{key}.json"
        entries[key] = manifest
    pairing = {
        "episode_id": "episode_00000000",
        "instance": "instance.json",
        "scene": "scene.json",
        "task_order": config["composition"]["task_orders"][0],
        "segments": refs,
    }

    def bridge_fixture(session, scene_file, previous_exit, next_entry, output_path):
        target = deepcopy(previous_exit)
        scene_objects(target)["robot"] = deepcopy(scene_objects(next_entry)["robot"])
        target["robot"] = deepcopy(next_entry["robot"])
        interface = canonical(output_path, previous_exit, target, "bridge")
        entry_path, exit_path = (
            output_path.with_name(output_path.stem + "_entry.json"),
            output_path.with_name(output_path.stem + "_exit.json"),
        )
        write_json(entry_path, previous_exit)
        write_json(exit_path, target)
        return {
            "identity": {},
            "task": {"role": "bridge_task"},
            "source": {"hdf5": session.paths.relative(output_path), "demo": 0, "frames": [0, 2]},
            "states": {
                "directory": session.paths.relative(output_path.parent),
                "entry": entry_path.name,
                "exit": exit_path.name,
            },
            "objects": {"used": [], "read": [], "write": [], "memory_bindings": {}},
            "interface": interface,
            "validation": {"entry": True, "exit": True, "empty_hands": True, "stable": True},
        }

    return session, pairing, bridge_fixture, entries


def test_middle_key_preserves_memory_and_previous_intervent(material):
    session, pairing, backend, _ = material
    recipe = read_json(compose_episode(session, pairing, backend))
    validate_annotations(recipe)
    assert recipe["total_frames"] == 14
    assert [segment["role"] for segment in recipe["annotations"]] == [
        "intervent_task",
        "bridge_task",
        "key_task",
        "bridge_task",
        "intervent_task",
        "bridge_task",
        "query_task",
    ]
    with h5py.File(session.paths.resolve(recipe["hdf5"]), "r") as stream:
        group = stream["data/demo_0"]
        query_start = recipe["annotations"][-1]["frames"][0]
        state = scene_objects(json.loads(group["state_json"][query_start]))
        assert state["mug"]["root_link"]["pos"][0] == 3
        assert state["toy"]["root_link"]["pos"][0] == 1
        assert state["hammer"]["root_link"]["pos"][0] == 2
        assert scene_objects(json.loads(group["state_json"][-1]))["mug"]["root_link"]["pos"][0] == 0
        assert len(group["state_json"]) == len(group["action"]) + 1
        assert group["instruction"].asstr()[query_start - 1] == "Retrieve the mug."
    assert recipe["memory_spans"][0]["key_end_frame"] == 6
    with pytest.raises(FileExistsError):
        compose_episode(session, pairing, backend)


def test_multi_key_and_shared_state_dependencies(material):
    session, _, _, _ = material
    session.config = deepcopy(session.config)
    session.segments["key_2"] = {
        "role": "key_task",
        "activity": "update",
        "room_id": "kitchen_0",
        "depends_on": ["key_1"],
    }
    session.segments["query"]["depends_on"] = ["key_1", "key_2"]
    session.config["benchmark"]["level"] = 2
    session.config["collection"]["key_segment_ids"] = ["key_1", "key_2"]
    session.config["composition"]["task_orders"] = [["intervent_1", "key_1", "key_2", "intervent_2", "query"]]
    session.validate()
    with pytest.raises(ValueError, match="Unmet dependency"):
        session.validate_order(["intervent_1", "key_2", "key_1", "intervent_2", "query"])
    session.segments["intervent_2"]["room_id"] = "bedroom_0"
    with pytest.raises(ValueError, match="distinct room"):
        session.validate()


def test_rejects_incompatible_query_state(material):
    session, pairing, backend, entries = material
    query = entries["query"]
    entry = read_json(session.paths.resolve(query["states"]["entry"]))
    scene_objects(entry)["mug"]["root_link"]["pos"][0] = 9
    session.paths.resolve(query["states"]["entry"]).write_text(json.dumps(entry))
    with pytest.raises(ValueError, match="query preconditions"):
        compose_episode(session, pairing, backend)
    assert not list((session.paths.output_root / "recipes").glob("**/*.json"))


def test_rejects_missing_terminal_state(tmp_path):
    path = tmp_path / "source.hdf5"
    state = snapshot({"robot": 0})
    canonical(path, state, state, "test", terminal=False)
    with pytest.raises(ValueError, match="terminal"):
        FrameReader(path)


def test_robot_boundary_uses_physical_base_pose():
    entry = snapshot({"robot": 0})
    actual = deepcopy(entry)
    # R1Pro's serialized root may remain fixed while virtual base joints move it.
    actual["robot"]["position"] = [2, 0, 0]
    tolerance = {"ignored_fields": [], "numeric": {"default": 0.001, "pos": 0.02, "ori": 0.05}}
    with pytest.raises(ValueError, match="robot pose"):
        require_robot_match(actual, entry, "robot", tolerance, "Endpoint")
    renamed = mapped_snapshot(entry, "robot", "r1pro")
    assert renamed["robot"]["name"] == "r1pro"
    assert "r1pro" in scene_objects(renamed) and "robot" not in scene_objects(renamed)


def test_legacy_recording_schema_is_upgraded_without_changing_source(tmp_path):
    scene = snapshot({"robot": 0})
    scene["objects_info"]["init_info"]["robot"].update(class_module="omnigibson.robots.r1pro", class_name="R1Pro")
    scene_objects(scene)["robot"]["controllers"] = {"base": {"goal_is_valid": True, "goal": {"target": [0, 0, 0]}}}
    path = tmp_path / "legacy.hdf5"
    canonical(path, scene, scene, "test")
    original_hash = file_hash(path)
    metadata = recording_metadata(path)
    initializer = metadata["scene"]["objects_info"]["init_info"]["robot"]
    assert initializer["class_name"] == "Robot" and initializer["args"]["model"] == "r1pro"
    assert scene_objects(metadata["scene"])["robot"]["controller_groups"]["base"]["goal_set"]
    assert file_hash(path) == original_hash


def test_segment_rejects_transient_protected_change(material):
    session, _, _, entries = material
    segment = entries["intervent_1"]
    source_path = session.paths.resolve(segment["source"]["hdf5"])
    with h5py.File(source_path, "a") as stream:
        state = json.loads(stream["data/demo_0/state_json"][1])
        scene_objects(state)["mug"]["root_link"]["pos"][0] = 4
        stream["data/demo_0/state_json"][1] = json.dumps(state)
    with pytest.raises(ValueError, match="Undeclared state changes"):
        make_segment(
            session,
            "intervent_1",
            1,
            segment["source"],
            segment["states"],
            segment["objects"],
            segment["validation"],
            session.paths.output_root / "checked.json",
        )
