from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import h5py
import pytest
import torch as th
import yaml

from memory_bench.session import Session, SessionPaths
from memory_bench.tasks import TASKS
from memory_bench.tasks.base_task import MemoryTask
from memory_bench.tests.helpers import freeze_intervent_sources, make_scene_snapshot, write_recording
from memory_bench.utils.scene.instances import build_instance_tro, prepared_scene
from memory_bench.utils.scene.state import merge_scene_assets, read_json, scene_objects, write_json


@pytest.fixture
def memory_task(monkeypatch):
    """Use a small task to exercise the pipeline independently of benchmark examples."""
    roles = ("mug.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "countertop.n.01_1")
    usage = {name: roles for name in ("used", "read", "write", "protected")}
    task = MemoryTask(
        task_name="test_retrieve_item",
        scene_model="house_double_floor_lower",
        room_types=("kitchen",),
        whitelist={"mug.n.04": {"mug": {"pskrgy": None}}},
        key_activities=("test_store_item",),
        intervent_activities=("turning_on_radio", "putting_shoes_on_rack"),
        query_activity="test_retrieve_item",
        query_instruction="Retrieve the mug.",
        key_instructions={variant: {"test_store_item": "Put the mug in the cabinet."} for variant in ("A", "B")},
        variants={
            "A": {"cabinet.n.01_1": "top_cabinet_tynnnw_0", "cabinet.n.01_2": "top_cabinet_tynnnw_1"},
            "B": {"cabinet.n.01_1": "top_cabinet_tynnnw_1", "cabinet.n.01_2": "top_cabinet_tynnnw_0"},
        },
        object_usage={"test_store_item": usage, "test_retrieve_item": usage},
        init={variant: {
            "key_1": [["ontop", roles[0], roles[3]]],
            "query": [["inside", roles[0], roles[1]]],
        } for variant in ("A", "B")},
        goal={variant: {
            "key_1": [["inside", roles[0], roles[1]]],
            "query": [["ontop", roles[0], roles[3]]],
        } for variant in ("A", "B")},
    )
    monkeypatch.setitem(TASKS, task.task_name, task)
    return task


@pytest.fixture
def sampling_config(memory_task):
    config = yaml.safe_load(Path("memory_bench/configs/mb000_retrieve_milk.yaml").read_text())
    config["session_id"] = memory_task.task_name
    config["segments"]["key_1"]["activity"] = memory_task.key_activities[0]
    config["segments"]["query"]["activity"] = memory_task.query_activity
    config["assets"]["requirements"] = {"mug": {"source": "insert", "instances": ["mug.n.04_1"]}}
    return config


@pytest.fixture
def episode_sources(tmp_path, memory_task):
    query = "test_retrieve_item"
    definitions = {
        "key_1": {"role": "key_task", "activity": "test_store_item", "room_id": "kitchen_0", "depends_on": []},
        "intervent_1": {
            "role": "intervent_task",
            "activity": "turning_on_radio",
            "room_id": "living_room_0",
            "depends_on": [],
        },
        "intervent_2": {
            "role": "intervent_task",
            "activity": "putting_shoes_on_rack",
            "room_id": "corridor_0",
            "depends_on": [],
        },
        "query": {"role": "query_task", "activity": query, "room_id": "kitchen_0", "depends_on": ["key_1"]},
    }
    config = {
        "session_id": query,
        "benchmark": {"level": 1},
        "memory_room": "kitchen_0",
        "scene_model": "house_double_floor_lower",
        "instance_sampling": {"count": 1},
        "segments": definitions,
        "composition": {
            "demo_id": 1, "order_seed": 1, "instance_id": None,
            "task_orders": [["intervent_1", "key_1", "intervent_2", "query"]],
        },
        "assets": {},
    }
    session = Session(config, SessionPaths(tmp_path, tmp_path, tmp_path, query, config["scene_model"]))
    session.validate()
    positions = {"robot": 0, "item": 0, "cabinet": 3, "table": 0, "toy": 0, "hammer": 0}
    scene = make_scene_snapshot(positions)
    write_json(tmp_path / "scene.json", scene)
    write_json(
        session.paths.session_dir / "assets.json",
        {
            "protected": ["item", "cabinet", "table"],
            "intervent_objects": {"intervent_1": ["toy"], "intervent_2": ["hammer"]},
        },
    )
    assignments_path = session.paths.session_dir / "variant_assignments.json"
    assignment = {
        "instance_id": 1,
        "variant_id": "A",
        "bindings": {"key_1": {"mug.n.04_1": "item"}, "query": {"mug.n.04_1": "item"}},
    }
    write_json(assignments_path, {
        "session_id": query, "instances": [assignment],
        "asset_requirements": {"memory": ["item", "cabinet", "table"]},
    })
    scene["init_info"] = {"args": {"scene_file": None}}
    refs = {}
    entries = {}
    for index, (key, write, value) in enumerate(
        (("key_1", "item", 3), ("intervent_1", "toy", 1), ("intervent_2", "hammer", 2), ("query", "item", 0))
    ):
        entry_positions = {**positions, "robot": index + 1}
        if key == "query":
            entry_positions["item"] = 3
        entry = make_scene_snapshot(entry_positions)
        exit_state = make_scene_snapshot({**entry_positions, write: value})
        if key.startswith("intervent"):
            entry["metadata"]["task"]["inst_to_name"] = {f"{write}.n.01_1": write}
            entry["objects_info"]["init_info"][write]["args"]["in_rooms"] = [definitions[key]["room_id"]]
        path = tmp_path / f"{key}.hdf5"
        interface = write_recording(path, entry, exit_state, definitions[key]["activity"])
        write_json(tmp_path / f"{key}_entry.json", entry)
        write_json(tmp_path / f"{key}_exit.json", exit_state)
        used = [write] if key.startswith("intervent") else ["item", "cabinet", "table"]
        manifest = {
            "identity": {"id": key, "instance_id": 1},
            "task": {**definitions[key], "instruction": "Retrieve the mug." if key == "query" else key},
            "source": {"hdf5": str(path.resolve()) if key.startswith("intervent") else path.name, "demo": 0, "frames": [0, 2], "fps": 30},
            "states": {"directory": ".", "entry": f"{key}_entry.json", "exit": f"{key}_exit.json"},
            "objects": {
                "used": used,
                "read": used,
                "write": [write],
                "memory_bindings": {"mug.n.04_1": "item"} if not key.startswith("intervent") else {},
            },
            "interface": interface,
            "validation": {"entry": True, "exit": True, "empty_hands": True, "stable": True},
        }
        write_json(tmp_path / f"{key}.json", manifest)
        refs[key] = manifest
        entries[key] = manifest
    i_refs = {key: path for key, path in refs.items() if key.startswith("intervent")}
    allocation_path = freeze_intervent_sources(session, {"1": i_refs})
    template = prepared_scene(scene, "robot")
    for key in i_refs:
        template = merge_scene_assets(template, read_json(tmp_path / f"{key}_entry.json"), entries[key]["objects"]["used"])
    template["metadata"]["task"]["memory_bench_inputs"] = {
        "assignments": str(assignments_path.relative_to(session.paths.output_root)),
        "intervents": str(allocation_path.relative_to(session.paths.output_root)),
    }
    write_json(tmp_path / "scene_template.json", template)
    write_json(tmp_path / "instance.json", build_instance_tro(template, template, {
        "session_id": query, "scene_template": "scene_template.json",
        "instance_id": 1,
        "robot": {key: scene["robot"][key] for key in ("position", "orientation")},
    }))
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
        interface = write_recording(output_path, previous_exit, target, "bridge")
        entry_path, exit_path = (
            output_path.with_name(output_path.stem + "_entry.json"),
            output_path.with_name(output_path.stem + "_exit.json"),
        )
        write_json(entry_path, previous_exit)
        write_json(exit_path, target)
        return {
            "identity": {},
            "task": {"role": "bridge_task", "activity": "bridge_navigation"},
            "source": {"hdf5": str(output_path.relative_to(session.paths.output_root)), "demo": 0, "frames": [0, 2]},
            "states": {
                "directory": str(output_path.parent.relative_to(session.paths.output_root)),
                "entry": entry_path.name,
                "exit": exit_path.name,
            },
            "objects": {"used": [], "read": [], "write": [], "memory_bindings": {}},
            "interface": interface,
            "validation": {"entry": True, "exit": True, "empty_hands": True, "stable": True},
        }

    return session, pairing, bridge_fixture, entries


@pytest.fixture
def collected_episode(episode_sources, monkeypatch):
    session, _, _, manifests = episode_sources
    monkeypatch.setattr("memory_bench.episodes.generate_bridges.generate_bridge", episode_sources[2])
    assignments_path = session.paths.session_dir / "variant_assignments.json"
    assignment = read_json(assignments_path)["instances"][0]
    tro = read_json(session.paths.output_root / "instance.json")
    tro_path = assignments_path.parent / "tro" / "instance_0001.json"
    write_json(tro_path, tro)
    usage = {
        definition["activity"]: {
            "used": ["mug.n.04_1"], "read": ["mug.n.04_1"],
            "write": ["mug.n.04_1"], "protected": ["mug.n.04_1"],
        }
        for key, definition in session.segments.items() if key in [*session.key_ids, session.query_id]
    }
    monkeypatch.setattr(Session, "task", property(lambda _: SimpleNamespace(
        object_usage=usage, instruction_for=lambda activity, variant_id: activity,
    )))
    raw_root = session.paths.recordings_dir / "instance_0001"

    def run(number=1, status="complete", joint_positions=None, robot_positions=None):
        directory = raw_root / f"demo_{number:03d}"
        segments = {}
        for key in [*session.key_ids, session.query_id]:
            manifest = manifests[key]
            entry = read_json(session.paths.resolve(manifest["states"]["entry"]))
            exit_state = read_json(session.paths.resolve(manifest["states"]["exit"]))
            # All subtasks start from the same full robot state after the transfer restore.
            for boundary in (entry, exit_state):
                base = read_json(session.paths.output_root / "scene.json")
                boundary["state"]["registry"]["object_registry"]["robot"] = base["state"]["registry"]["object_registry"]["robot"]
                boundary["robot"] = {**base["robot"], **tro["robot"]}
                if robot_positions is not None:
                    boundary["robot"]["position"] = [robot_positions[key], 0, 0]
                    scene_objects(boundary)["robot"]["root_link"]["pos"] = [robot_positions[key], 0, 0]
                if joint_positions is not None:
                    boundary["robot"]["joint_positions"] = list(joint_positions)
                    boundary["robot"]["controlled_joint_indices"] = list(range(len(joint_positions)))
                    boundary["state"]["registry"]["object_registry"]["robot"]["joint_pos"] = list(joint_positions)
            filename = "key_01" if key == "key_1" else key
            source = directory / f"{filename}.hdf5"
            write_recording(source, entry, exit_state, session.segments[key]["activity"], terminal=False)
            with h5py.File(source, "a") as stream:
                stream["data"].attrs.update(
                    session_id=session.session_id, instance_id=1, variant_id="A", segment_id=key,
                    run_id=directory.name, collection_complete=True,
                )
            write_json(directory / f"{filename}_entry.json", entry)
            write_json(directory / f"{filename}_exit.json", exit_state)
            segments[key] = {
                "hdf5": str(source.relative_to(session.paths.output_root)), "demo": 0, "frames": [0, 2],
                "states": {"directory": str(directory.relative_to(session.paths.output_root)),
                           "entry": f"{filename}_entry.json", "exit": f"{filename}_exit.json"},
                "validation": {"entry": True, "exit": True, "empty_hands": True, "stable": True,
                               "collision_free": True},
            }
        record = {
            "format": "memory_bench.collection_run.v1", "run_id": directory.name,
            "session_id": session.session_id, "instance_id": 1,
            "variant_id": "A", "instance": str(tro_path.relative_to(session.paths.output_root)),
            "status": status, "segment_ids": [*session.key_ids, session.query_id], "segments": segments,
            "transfers": [{"from_segment": "key_1", "to_segment": "query",
                           "validation": {"nonrobot_world_preserved": True, "entry_pose": True}}],
        }
        path = directory / "manifest.json"
        write_json(path, record)
        return path, record

    inventory = {"session_id": session.session_id, "tasks": {}}
    for key, definition in session.segments.items():
        if definition["role"] != "intervent_task":
            continue
        inventory["tasks"][key] = {"candidates": [
            {"hdf5": str(session.paths.resolve(manifests[key]["source"]["hdf5"])), "demo": 0,
             "used": manifests[key]["objects"]["used"], "interface": manifests[key]["interface"]}
        ]}
    inventory_path = session.paths.session_dir / "source_inventory.json"
    write_json(inventory_path, inventory)

    tro["bindings"] = assignment["bindings"]
    tro["intervents"] = read_json(assignments_path.parent / "intervent_assignments.json")["instances"]["1"]
    return session, {**assignment, "robot": tro["robot"]}, tro, run, inventory_path


@pytest.fixture
def sparse_segment_wrapper(tmp_path, monkeypatch):
    import omnigibson as og
    from omnigibson.objects.usd_object import USDObject

    from memory_bench.utils.recording.wrappers import SegmentRecordingWrapper

    class Object:
        is_active = USDObject.is_active

        def __init__(self, name, index):
            self.name, self.index, self.value = name, index, 0.0
            self.kinematic_only, self.is_asleep = False, True
            self.initialized = True

    objects = {name: Object(name, index) for index, name in enumerate(("robot", "moving", "cabinet", "idle"))}
    registry = SimpleNamespace(_dump_filter=lambda obj: True)
    registry.set_dump_filter = lambda dump_filter: setattr(registry, "_dump_filter", dump_filter)
    scene = SimpleNamespace(objects=list(objects.values()), robots=[objects["robot"]], object_registry=registry,
                            updated_state_objects=set())
    for obj in objects.values():
        obj.scene = scene

    def dump_state(serialized=True):
        selected = [obj for obj in scene.objects if registry._dump_filter(obj)]
        return th.tensor([len(selected), *[value for obj in selected for value in (obj.index, obj.value)]])

    def deserialize(flat):
        values = {}
        for index in range(int(flat[0])):
            obj = scene.objects[int(flat[1 + 2 * index])]
            values[obj.name] = {"value": [float(flat[2 + 2 * index])]}
        return [{"registry": {"object_registry": values, "system_registry": {}}}], 1 + 2 * int(flat[0])

    scene.save = lambda **kwargs: {obj.name: obj.value for obj in scene.objects}

    def restore(state):
        for name, value in state.items():
            objects[name].value = value

    scene.restore = restore
    env = SimpleNamespace(scene=scene, config={"task": {"type": "DummyTask"}}, episode_steps=th.zeros(1),
                          _current_steps=th.zeros(1), reset=lambda: ([{}], {}))
    env.step_update = lambda: None

    def step(action, **kwargs):
        # Match Simulator.step: clear old dirty flags before updating object states.
        scene.updated_state_objects.clear()
        env.step_update()
        return [{}], th.zeros(1), th.zeros(1, dtype=th.bool), th.zeros(1, dtype=th.bool), [{}]

    env.step = step
    wrapper = SegmentRecordingWrapper.__new__(SegmentRecordingWrapper)
    wrapper.wrapped_obj = env
    wrapper.env = env
    wrapper._enable_dump_filters = False
    wrapper._clear_segment()
    # Viewport optimization is unrelated to state collection and requires a running app.
    wrapper._optimize_sim_for_data_collection = Mock()
    sim = SimpleNamespace(dump_state=dump_state, deserialize=deserialize,
                          viewer_camera=SimpleNamespace(active_camera_path="camera"))
    monkeypatch.setattr(og, "sim", sim)
    wrapper.begin_segment(tmp_path / "segment.hdf5", {}, {"activity_name": "test_activity"})
    return wrapper, objects, sim

