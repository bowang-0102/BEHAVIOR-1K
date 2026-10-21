"""Offline regression checks for boundaries, memory inheritance and dependency ordering."""

import json
import sys
from copy import deepcopy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import h5py
import numpy as np
import pytest
import torch as th

from memory_bench.episodes.compose_episodes import write_sequence
from memory_bench.episodes.render_episodes import validate_annotations
from memory_bench.episodes.synthesize_bridges import (
    _assert_world_preserved,
    _robot_contact_diagnostics,
    _scene_snapshot,
    synthesize_bridge,
    _load_terminal_attempt,
    _seed_terminal_history,
)
from memory_bench.episodes.build_bridges import connect_segments
from memory_bench.session import Session
from memory_bench.utils.instance_scene import build_instance_tro, prepared_scene
from memory_bench.utils.paths import SessionPaths
from memory_bench.utils.recordings import FrameReader, recording_metadata
from memory_bench.utils.scene_state import (
    get_state_tolerances,
    read_json,
    merge_scene_assets,
    require_robot_match,
    scene_objects,
    state_matches,
    write_json,
)
from memory_bench.utils.segment_manifest import load_segments, mapped_snapshot


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
        "scene": {"scene_model": "house_double_floor_lower"},
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


def freeze_intervents(session, instances):
    root = session.paths.resolve(f"sessions/{session.session_id}")
    sources = {}
    for instance_id, refs in instances.items():
        sources[instance_id] = {}
        for key, value in refs.items():
            manifest = read_json(session.paths.resolve(value)) if isinstance(value, str) else value
            source = manifest["source"] if "source" in manifest else manifest
            sources[instance_id][key] = {name: source[name] for name in ("hdf5", "demo")}
    path = root / "intervent_assignments.json"
    write_json(path, {"session_id": session.session_id, "instances": sources})
    return path


@pytest.fixture
def material(tmp_path, monkeypatch):
    query = "mb_retrieve_chocolate_from_top_cabinet"
    definitions = {
        "key_1": {"role": "key_task", "activity": "mb_chocolate_into_top_cabinet", "room_id": "kitchen_0", "depends_on": []},
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
        "collection": {"key_segment_ids": ["key_1"]},
        "composition": {"task_orders": [["intervent_1", "key_1", "intervent_2", "query"]]},
        "assets": {"manifest": "assets.json"},
    }
    session = Session(config, SessionPaths(tmp_path, tmp_path, tmp_path))
    session.validate()
    monkeypatch.setattr("memory_bench.tasks.get_task", lambda _: SimpleNamespace(key_information={"key_1": "location"}))
    positions = {"robot": 0, "chocolate": 0, "cabinet": 3, "table": 0, "toy": 0, "hammer": 0}
    scene = snapshot(positions)
    write_json(tmp_path / "scene.json", scene)
    write_json(
        tmp_path / "assets.json",
        {
            "protected": ["chocolate", "cabinet", "table"],
            "intervent_objects": {"intervent_1": ["toy"], "intervent_2": ["hammer"]},
        },
    )
    assignments_path = session.paths.resolve(f"sessions/{query}/case_assignments.json")
    assignment = {
        "instance_id": 1,
        "case_id": "A",
        "bindings": {"key_1": {"chocolate_bar.n.01_1": "chocolate"}, "query": {"chocolate_bar.n.01_1": "chocolate"}},
    }
    write_json(assignments_path, {
        "session_id": query, "instances": [assignment],
        "asset_requirements": {"memory": ["chocolate", "cabinet", "table"]},
    })
    scene["init_info"] = {"args": {"scene_file": None}}
    refs = {}
    entries = {}
    for index, (key, write, value) in enumerate(
        (("key_1", "chocolate", 3), ("intervent_1", "toy", 1), ("intervent_2", "hammer", 2), ("query", "chocolate", 0))
    ):
        entry_positions = {**positions, "robot": index + 1}
        if key == "query":
            entry_positions["chocolate"] = 3
        entry = snapshot(entry_positions)
        exit_state = snapshot({**entry_positions, write: value})
        if key.startswith("intervent"):
            entry["metadata"]["task"]["inst_to_name"] = {f"{write}.n.01_1": write}
            entry["objects_info"]["init_info"][write]["args"]["in_rooms"] = [definitions[key]["room_id"]]
        path = tmp_path / f"{key}.hdf5"
        interface = canonical(path, entry, exit_state, definitions[key]["activity"])
        write_json(tmp_path / f"{key}_entry.json", entry)
        write_json(tmp_path / f"{key}_exit.json", exit_state)
        used = [write] if key.startswith("intervent") else ["chocolate", "cabinet", "table"]
        manifest = {
            "identity": {"id": key, "instance_id": 1},
            "task": {**definitions[key], "instruction": "Retrieve the chocolate bar." if key == "query" else key},
            "source": {"hdf5": path.name, "demo": 0, "frames": [0, 2], "fps": 30},
            "states": {"directory": ".", "entry": f"{key}_entry.json", "exit": f"{key}_exit.json"},
            "objects": {
                "used": used,
                "read": used,
                "write": [write],
                "memory_bindings": {"chocolate_bar.n.01_1": "chocolate"} if not key.startswith("intervent") else {},
            },
            "interface": interface,
            "validation": {"entry": True, "exit": True, "empty_hands": True, "stable": True},
        }
        write_json(tmp_path / f"{key}.json", manifest)
        refs[key] = manifest
        entries[key] = manifest
    i_refs = {key: path for key, path in refs.items() if key.startswith("intervent")}
    allocation_path = freeze_intervents(session, {"1": i_refs})
    template = prepared_scene(scene, "robot")
    for key in i_refs:
        template = merge_scene_assets(template, read_json(tmp_path / f"{key}_entry.json"), entries[key]["objects"]["used"])
    template["metadata"]["task"]["memory_bench_inputs"] = {
        "assignments": session.paths.relative(assignments_path),
        "intervents": session.paths.relative(allocation_path),
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


def test_intervent_only_sequence_preserves_inherited_world(material, monkeypatch):
    original, pairing, backend, _ = material
    order = ["intervent_1", "intervent_2"]
    # The shared composition core must run without collection, instance or memory-task configuration.
    session = Session(
        {
            "session_id": "intervent_debug",
            "segments": {key: original.segments[key] for key in order},
        },
        original.paths,
    )

    def unexpected_memory_task(_):
        pytest.fail("I-only composition must not load a K/Q task")

    monkeypatch.setattr("memory_bench.tasks.get_task", unexpected_memory_task)
    references = {key: pairing["segments"][key] for key in order}
    segments = load_segments(session, references)
    scene = read_json(session.paths.resolve(segments[order[0]]["states"]["entry"]))

    def inherited_world_bridge(session, scene_file, previous_exit, next_entry, output_path):
        for world in (scene_file, previous_exit):
            objects = scene_objects(world)
            assert objects["toy"]["root_link"]["pos"][0] == 1
            assert objects["hammer"]["root_link"]["pos"][0] == 0
        return backend(session, scene_file, previous_exit, next_entry, output_path)

    assembled, bridge_refs = connect_segments(
        session, scene, order, segments, "episode_00000000", bridge_builder=inherited_world_bridge
    )
    recipe = write_sequence(session, scene, assembled, set(), "episode_00000000", {})
    validate_annotations(recipe)
    assert recipe["task_order"] == order
    assert list(bridge_refs) == ["bridge_00"]
    assert recipe["memory_spans"] == []
    assert recipe["total_frames"] == 6
    assert [(item["id"], item["frames"]) for item in recipe["annotations"]] == [
        ("intervent_1", [0, 2]),
        ("bridge_00", [2, 4]),
        ("intervent_2", [4, 6]),
    ]
    with h5py.File(session.paths.resolve(recipe["hdf5"]), "r") as stream:
        group = stream["data/demo_0"]
        states = [scene_objects(json.loads(value)) for value in group["state_json"]]
        assert len(states) == len(group["action"]) + 1 == 7
        assert [state["toy"]["root_link"]["pos"][0] for state in states] == [0, 0, 1, 1, 1, 1, 1]
        assert [state["hammer"]["root_link"]["pos"][0] for state in states] == [0, 0, 0, 0, 0, 0, 2]
        assert states[-1]["robot"]["root_link"]["pos"][0] == 3
        assert group["instruction"].asstr()[:].tolist() == ["intervent_1"] * 2 + ["intervent_2"] * 4


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
    session.segments["intervent_2"]["room_id"] = "living_room_0"
    with pytest.raises(ValueError, match="distinct room"):
        session.validate()


def test_rejects_missing_terminal_state(tmp_path):
    path = tmp_path / "source.hdf5"
    state = snapshot({"robot": 0})
    canonical(path, state, state, "test", terminal=False)
    with pytest.raises(ValueError, match="terminal"):
        FrameReader(path)


def test_state_comparison_tolerates_jitter_and_rejects_displacement():
    expected = {"pos": [0, 0, 0], "lin_vel": [0, 0, 0]}
    actual = {"pos": [0.01, 0, 0], "lin_vel": [0.1, 0, 0]}
    assert state_matches(actual, expected, get_state_tolerances())
    resolved = get_state_tolerances()
    resolved["ignored_fields"].append("joint_vel")
    resolved["numeric"]["pos"] = 1.0
    assert not state_matches({"pos": [0.1, 0, 0]}, {"pos": [0, 0, 0]}, get_state_tolerances())


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
    original = path.read_bytes()
    metadata = recording_metadata(path)
    initializer = metadata["scene"]["objects_info"]["init_info"]["robot"]
    assert initializer["class_name"] == "Robot" and initializer["args"]["model"] == "r1pro"
    assert scene_objects(metadata["scene"])["robot"]["controller_groups"]["base"]["goal_set"]
    assert path.read_bytes() == original


def test_bridge_guard_keeps_complete_live_state_without_resaving_metadata(material, monkeypatch):
    session, _, _, _ = material
    original = read_json(session.paths.output_root / "scene.json")
    refresh = Mock()
    monkeypatch.setitem(
        sys.modules,
        "omnigibson.object_states.tensorized_state",
        SimpleNamespace(TensorizedState=SimpleNamespace(maybe_refresh_caches=refresh)),
    )
    monkeypatch.setattr(
        "memory_bench.episodes.synthesize_bridges.robot_metadata", lambda _: deepcopy(original["robot"])
    )
    current = deepcopy(original["state"])
    scene = SimpleNamespace(robots=[object()], save=Mock(return_value=original), dump_state=Mock(return_value=current))
    entry = _scene_snapshot(scene, json.JSONEncoder)
    scene_objects(current)["toy"]["root_link"]["pos"][0] = 1
    live = _scene_snapshot(scene, json.JSONEncoder, metadata=entry)
    scene.save.assert_called_once_with(as_dict=True)
    scene.dump_state.assert_called_once_with(serialized=False)
    assert refresh.call_count == 2
    assert set(scene_objects(live)) == set(scene_objects(entry))
    assert live["objects_info"] == entry["objects_info"]
    assert scene_objects(entry)["toy"]["root_link"]["pos"][0] == 0
    with pytest.raises(RuntimeError, match="toy"):
        _assert_world_preserved(entry, live)
    scene_objects(current)["toy"]["root_link"]["pos"][0] = 0
    current["registry"]["system_registry"]["water"] = {"particles": [[0, 0, 0]]}
    live = _scene_snapshot(scene, json.JSONEncoder, metadata=entry)
    with pytest.raises(RuntimeError, match="topology"):
        _assert_world_preserved(entry, live)


def test_bridge_collision_diagnostics_uses_detector_ignores_and_live_contact_pairs(monkeypatch):
    robot = SimpleNamespace(
        name="robot", category="robot", links={"base": SimpleNamespace(name="base", prim_path="/robot")}
    )
    wall = SimpleNamespace(
        name="wall", category="walls", links={"body": SimpleNamespace(name="body", prim_path="/wall")}
    )
    robot.scene = SimpleNamespace(idx=0, objects=[robot, wall])

    def detector(robot, filter_objs):
        filter_objs.extend(["/robot", "/ground", "/held"])
        return True

    pairs = {("/robot", name) for name in ("/robot", "/ground", "/held", "/wall")}
    contacts = SimpleNamespace(
        _get_prim_paths=Mock(side_effect=set),
        get_contact_pairs=Mock(return_value=pairs),
    )
    monkeypatch.setitem(
        sys.modules, "omnigibson.utils.motion_planning_utils", SimpleNamespace(detect_robot_collision_in_sim=detector)
    )
    monkeypatch.setitem(sys.modules, "omnigibson.utils.usd_utils", SimpleNamespace(RigidContactAPI=contacts))
    result = _robot_contact_diagnostics(robot)
    assert result["collision_detected"]
    assert result["contact_pairs"] == [("/robot", "/wall")]
    assert result["all_robot_contact_pairs"] == sorted(pairs)
    assert result["link_owners"]["/wall"]["object"] == "wall"
    assert result["robot_links"] == {"base": "/robot"}
    contacts.get_contact_pairs.assert_called_once_with(
        scene_idx=0, query_set=[robot], with_set=None, current_only=False
    )


@pytest.mark.parametrize(("clear_on_failure", "reuse_env"), [(False, False), (True, False), (False, True)])
def test_failed_bridge_preserves_debug_actions_and_original_error(material, monkeypatch, clear_on_failure, reuse_env):
    session, _, _, _ = material
    previous = read_json(session.paths.output_root / "scene.json")
    previous["robot"].update(joint_positions=[0], controlled_joint_indices=[0])
    robot = SimpleNamespace(
        name="robot",
        model="r1pro",
        action_dim=23,
        _ag_obj_in_hand={"left": None},
        reset_joint_pos=th.zeros(1),
        get_joint_velocities=lambda: th.zeros(1),
        get_linear_velocity=lambda: th.zeros(3),
        get_angular_velocity=lambda: th.zeros(3),
    )
    scene = SimpleNamespace(
        robots=[robot],
        restore=Mock(),
        load_state=Mock(),
        save=Mock(return_value=previous),
        dump_state=Mock(return_value=previous["state"]),
    )
    env = SimpleNamespace(scene=scene, robots=[[robot]], env_config={"action_frequency": 30})
    sim = SimpleNamespace(
        physics_sim_view=SimpleNamespace(update_articulations_kinematic=Mock()),
        sync_physx_to_fabric=Mock(),
        _refresh_state_caches=Mock(),
        dump_state=lambda **kwargs: th.zeros(1),
    )
    og = SimpleNamespace(sim=sim, clear=Mock())
    constructor = Mock(return_value=env)

    class Collector:
        def __init__(self, **kwargs):
            assert kwargs["enable_dump_filters"] is False
            self.env, self.fps, self.max_state_size = env, 30, 0
            self.hdf5_file = h5py.File(kwargs["output_path"], "x")
            self.hdf5_file.create_group("data")
            self.current_traj_history = []

        def reset(self):
            pass

        def step(self, action):
            self.current_traj_history.append({"action": action.numpy()})

        def save_data(self):
            self.hdf5_file["data"].create_dataset(
                "action", data=[step["action"] for step in self.current_traj_history[1:]]
            )
            self.hdf5_file.close()

    modules = {
        "omnigibson": og,
        "omnigibson.envs": SimpleNamespace(Environment=constructor, HDF5CollectionWrapper=Collector),
        "omnigibson.macros": SimpleNamespace(gm=SimpleNamespace(unlocked=nullcontext)),
        "omnigibson.utils.config_utils": SimpleNamespace(TorchEncoder=json.JSONEncoder),
        "omnigibson.utils.python_utils": SimpleNamespace(recursively_convert_to_torch=lambda value: value),
        "omnigibson.utils.motion_planning_utils": SimpleNamespace(detect_robot_collision_in_sim=lambda robot: False),
        "omnigibson.object_states.tensorized_state": SimpleNamespace(
            TensorizedState=SimpleNamespace(maybe_refresh_caches=Mock())
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr("memory_bench.utils.paths.SessionPaths.activate", Mock())
    monkeypatch.setattr("memory_bench.episodes.synthesize_bridges._build_config", lambda *args: {})
    monkeypatch.setattr("memory_bench.episodes.synthesize_bridges.robot_metadata", lambda robot: previous["robot"])
    monkeypatch.setattr(
        "memory_bench.episodes.synthesize_bridges._robot_contact_diagnostics",
        Mock(side_effect=RuntimeError("contact diagnostic unavailable")),
    )

    def interrupted_stow(robot, step, target, world_guard, **velocity_limits):
        step(th.ones(23))
        world_guard()
        raise RuntimeError("original bridge failure")

    monkeypatch.setattr("memory_bench.navigation.move_joints", interrupted_stow)
    output = session.paths.output_root / "bridge_00.hdf5"
    with pytest.raises(RuntimeError, match="original bridge failure"):
        synthesize_bridge(
            session,
            previous,
            previous,
            previous,
            output,
            clear_on_failure=clear_on_failure,
            env=env if reuse_env else None,
        )
    if reuse_env:
        constructor.assert_not_called()
    else:
        constructor.assert_called_once()
    report = read_json(
        session.paths.output_root / "reports" / session.session_id / "bridge_attempts/bridge_00_0000.json"
    )
    assert report["recorded_steps"] == 1
    assert "original bridge failure" in report["traceback"]
    assert "contact diagnostic unavailable" in report["robot_contacts"]["diagnostic_error"]
    assert not output.exists()
    if clear_on_failure:
        assert report["artifacts"] == {}
        og.clear.assert_called_once()
    else:
        with h5py.File(session.paths.resolve(report["artifacts"]["hdf5"]), "r") as stream:
            np.testing.assert_array_equal(stream["data/action"][:], np.ones((1, 23)))
        assert session.paths.resolve(report["artifacts"]["entry"]).is_file()
        assert report["observed_scene_metadata_source"] == report["artifacts"]["entry"]
        assert report["artifact_preservation_error"] is None
        og.clear.assert_not_called()


def test_terminal_history_keeps_every_prefix_action_and_actual_state_n():
    prefix = {
        "action": np.asarray([[1, 2], [3, 4]], dtype=np.float32),
        "state": np.asarray([[10, 11, 0], [20, 21, 22]], dtype=np.float32),
        "state_size": np.asarray([2, 3]),
        "reward": np.asarray([0.1, 0.2]),
        "terminated": np.asarray([False, False]),
        "truncated": np.asarray([False, False]),
    }
    wrapper = SimpleNamespace(current_traj_history=[], max_state_size=0, step_count=0)
    terminal = th.tensor([30, 31, 32], dtype=th.float32)
    _seed_terminal_history(wrapper, prefix, terminal)
    history = wrapper.current_traj_history
    assert wrapper.step_count == 2 and len(history) == 3
    np.testing.assert_array_equal(history[0]["state"], [10, 11])
    np.testing.assert_array_equal(history[1]["action"], prefix["action"][0])
    np.testing.assert_array_equal(history[1]["state"], [20, 21, 22])
    np.testing.assert_array_equal(history[2]["action"], prefix["action"][-1])
    np.testing.assert_array_equal(history[2]["state"], terminal)
    # The writer truncates states to the action count: a real continuation makes
    # the recovered state[N] the pre-action state of the first new hold action.
    history.append({"action": th.tensor([5, 6]), "state": th.tensor([40, 41, 42])})
    np.testing.assert_array_equal(th.stack([item["action"] for item in history[1:]])[:2], prefix["action"])
    np.testing.assert_array_equal(history[:-1][-1]["state"], terminal)


def test_terminal_resume_rejects_wrong_failure_and_checkpoint_mismatch(material):
    session, _, _, _ = material
    session.config["bridge"] = {"action_frequency": 30}
    scene = read_json(session.paths.output_root / "scene.json")
    scene["objects_info"]["init_info"]["robot"]["args"].update(
        model="r1pro", controller_config={"base": {"name": "test"}}, action_normalize=False
    )
    archive = session.paths.output_root / "bridge_00_0000"
    archive.mkdir()
    source = archive / "bridge_00.hdf5"
    canonical(source, scene, scene, "bridge")
    with h5py.File(source, "a") as stream:
        stream["data"].attrs["n_steps"] = 2
        stream["data"].attrs["n_episodes"] = 1
        group = stream["data/demo_0"]
        group.create_dataset("state", data=np.asarray([[10, 11, 12], [20, 21, 22]], dtype=np.float32))
        group.create_dataset("state_size", data=np.asarray([3, 3]))
    entry, exit_path = archive / "bridge_00_entry.json", archive / "bridge_00_exit.json"
    write_json(entry, scene)
    write_json(exit_path, scene)
    report = {
        "accepted": False,
        "traceback": "Traceback (most recent call last):\nRuntimeError: R1Pro was moving too quickly at bridge exit\n",
        "recorded_steps": 2,
        "observed_scene": scene,
        "robot_contacts": {"collision_detected": False, "contact_pairs": []},
        "artifacts": {
            "hdf5": session.paths.relative(source),
            "entry": session.paths.relative(entry),
            "exit": session.paths.relative(exit_path),
        },
        "artifact_preservation_error": None,
    }
    path = archive.with_suffix(".json")
    write_json(path, report)
    _, _, prefix, provenance, _ = _load_terminal_attempt(session, path, scene, scene, "bridge_00")
    assert len(prefix["action"]) == provenance["prefix_actions"] == 2
    assert provenance["artifacts"]["hdf5"] == session.paths.relative(source)
    session.bridge["action_frequency"] = 60
    with pytest.raises(ValueError, match="robot/controller/frequency"):
        _load_terminal_attempt(session, path, scene, scene, "bridge_00")
    session.bridge["action_frequency"] = 30
    report["traceback"] = "Traceback (most recent call last):\nRuntimeError: Robot collision\n"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="sole terminal-stability"):
        _load_terminal_attempt(session, path, scene, scene, "bridge_00")
    report["traceback"] = (
        "Traceback (most recent call last):\nRuntimeError: R1Pro was moving too quickly at bridge exit\n"
    )
    report["observed_scene"] = deepcopy(scene)
    scene_objects(report["observed_scene"])["robot"]["root_link"]["lin_vel"] = [0.5, 0, 0]
    original_exit = read_json(exit_path)
    scene_objects(original_exit)["robot"]["root_link"]["lin_vel"] = [0, 0, 0]
    exit_path.write_text(json.dumps(original_exit))
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="including velocities"):
        _load_terminal_attempt(session, path, scene, scene, "bridge_00")
