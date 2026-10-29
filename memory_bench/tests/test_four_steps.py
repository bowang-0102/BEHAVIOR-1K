import builtins
import json
import sys
from copy import deepcopy
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import h5py
import numpy as np
import pytest

from memory_bench.collection.prepare_intervent import prepare_intervents
from memory_bench.episodes.build_episodes import build_episode
from memory_bench.episodes.compose_episodes import compose_episode
from memory_bench.episodes.generate_bridges import _assert_world_preserved
from memory_bench.session import Session
from memory_bench.tests.test_pipeline import canonical, material as material, snapshot
from memory_bench.utils.recordings import FrameReader, RecordingContext
from memory_bench.utils.scene_state import read_json, scene_objects, write_json
from memory_bench.utils.task_segments import check_frame


@pytest.fixture
def collected(material, monkeypatch):
    session, _, _, manifests = material
    monkeypatch.setattr("memory_bench.episodes.generate_bridges.generate_bridge", material[2])
    assignments_path = session.paths.session_dir / "variant_assignments.json"
    assignment = read_json(assignments_path)["instances"][0]
    tro = read_json(session.paths.output_root / "instance.json")
    tro_path = assignments_path.parent / "tro" / "instance_0001.json"
    write_json(tro_path, tro)
    usage = {
        definition["activity"]: {
            "used": ["box__of__baking_soda.n.01_1"], "read": ["box__of__baking_soda.n.01_1"],
            "write": ["box__of__baking_soda.n.01_1"], "protected": ["box__of__baking_soda.n.01_1"],
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
            canonical(source, entry, exit_state, session.segments[key]["activity"], terminal=False)
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


@pytest.mark.parametrize("demo_id", [1, 2])
def test_selects_only_requested_demo(collected, demo_id):
    session, _, _, run, _ = collected
    runs = {number: run(number)[0] for number in (1, 2)}
    session.composition["demo_id"] = demo_id
    path = build_episode(session, 1, session.composition["task_orders"][0])
    recipe = read_json(path)
    for key in [*session.key_ids, session.query_id]:
        source = session.paths.resolve(recipe["segments"][key]["source"]["hdf5"])
        assert source.parent == runs[demo_id].parent
    for manifest in runs.values():
        manifest.unlink()
    session.composition["demo_id"] = 3 - demo_id
    compose_episode(session, recipe)
    result = read_json(path)
    assert result["segments"] == recipe["segments"]
    assert "collection_manifest" not in result
    assert "collection_demo" not in result


def test_missing_first_demo_does_not_fall_back(collected):
    session, _, _, run, _ = collected
    run(2)
    with pytest.raises(FileNotFoundError):
        build_episode(session, 1, session.composition["task_orders"][0])


def test_incomplete_first_demo_does_not_fall_back(collected):
    session, _, _, run, _ = collected
    run(1, "in_progress")
    run(2)
    with pytest.raises(ValueError, match="incomplete"):
        build_episode(session, 1, session.composition["task_orders"][0])


def test_bridges_and_composition_are_independent_and_query_is_restorable(collected, material, monkeypatch):
    session, _, _, run, _ = collected
    manifest_path, _ = run(1)
    run(2)
    raw = {path: path.read_bytes() for path in session.paths.output_root.rglob("*.hdf5")}
    path = build_episode(session, 1, session.composition["task_orders"][0])
    recipe = read_json(path)
    assert session.paths.resolve(recipe["segments"]["key_1"]["source"]["hdf5"]).parent == manifest_path.parent
    assert len(recipe["bridges"]) == 3
    assert not session.paths.episodes_dir.exists()
    assert not session.paths.resolve("segments").exists()
    assert not list(session.paths.output_root.rglob("*normalized*"))
    assert all(source.read_bytes() == data for source, data in raw.items())
    monkeypatch.setattr("memory_bench.episodes.generate_bridges.generate_bridge", Mock(side_effect=AssertionError))
    compose_episode(session, recipe)
    result = read_json(path)
    assert result["total_frames"] == 14
    restored = read_json(session.paths.resolve(result["query_start"]))
    with h5py.File(session.paths.resolve(result["hdf5"]), "r") as stream:
        assert restored["state"] == json.loads(stream["data/demo_0/state_json"][result["query_start_frame"]])
        assert len(stream["data/demo_0/state_json"]) == len(stream["data/demo_0/action"]) + 1
    objects = scene_objects(restored)
    assert objects["baking_soda"]["root_link"]["pos"][0] == 3
    assert objects["toy"]["root_link"]["pos"][0] == 1
    assert objects["hammer"]["root_link"]["pos"][0] == 2
    assert "robot" in restored["objects_info"]["init_info"]
    assert restored["robot"]["name"] == "robot"
    assert restored["metadata"]["task"]["inst_to_name"] == read_json(
        (session.paths.session_dir / "variant_assignments.json")
    )["instances"][0]["bindings"]["query"]
    assert not list(session.paths.output_root.rglob("segment_index.json"))
    assert not list(session.paths.output_root.rglob("material_groups.json"))


@pytest.mark.parametrize("segment_id", ["key_1", "intervent_1"])
@pytest.mark.parametrize("first_segment", [False, True])
def test_selected_entry_survives_bridge_build_and_composition(collected, material, segment_id, first_segment):
    session, _, _, run, _ = collected
    _, record = run()
    item = record["segments"][segment_id] if segment_id == "key_1" else material[3][segment_id]
    source = session.paths.resolve(item["hdf5"] if segment_id == "key_1" else item["source"]["hdf5"])
    boundary_dir = session.paths.resolve(item["states"]["directory"])
    original_entry = read_json(boundary_dir / item["states"]["entry"])
    original_x = original_entry["robot"]["position"][0]
    operated_object = "baking_soda" if segment_id == "key_1" else "toy"
    destination = 3 if segment_id == "key_1" else 1
    states = []
    for frame in range(61):
        state = deepcopy(original_entry["state"])
        objects = scene_objects(state)
        objects["robot"].update(joint_vel=[0.0] * 28, ag_obj_constraint_params={})
        objects["robot"]["root_link"]["pos"][0] = original_x + min(frame, 20) / 20
        if frame < 20:
            objects["robot"]["joint_vel"][0] = 1.5
        if frame >= 40:
            objects[operated_object]["root_link"]["pos"][0] = destination
        states.append(state)
    with h5py.File(source, "a") as stream:
        group = stream["data/demo_0"]
        for field in ("state_json", "action", "reward", "terminated", "truncated"):
            del group[field]
        recorded_states = states[:-1] if segment_id == "key_1" else states
        group.create_dataset(
            "state_json", data=[json.dumps(state) for state in recorded_states], dtype=h5py.string_dtype()
        )
        group.create_dataset("action", data=np.repeat(np.arange(60, dtype=np.float32)[:, None], 23, axis=1))
        for field in ("reward", "terminated", "truncated"):
            group.create_dataset(field, data=np.zeros(60))
    exit_state = read_json(boundary_dir / item["states"]["exit"])
    exit_state["state"] = states[-1]
    exit_state["robot"]["position"] = [original_x + 1, 0, 0]
    (boundary_dir / item["states"]["exit"]).write_text(json.dumps(exit_state))
    if segment_id == "key_1":
        item["frames"] = [0, 60]
        (source.parent / "manifest.json").write_text(json.dumps(record))

    order = list(session.composition["task_orders"][0])
    if (order[0] == segment_id) != first_segment:
        order[:2] = reversed(order[:2])
    session.composition["task_orders"] = [order]
    recipe_path = build_episode(session, 1, order)
    recipe = read_json(recipe_path)
    assert recipe["segments"][segment_id]["source"]["frames"] == [20, 60]
    assert recipe["segments"]["query"]["source"]["frames"] == [0, 2]
    if not first_segment:
        bridge = recipe["bridges"]["bridge_00"]
        endpoint = read_json(session.paths.resolve(bridge["states"]["directory"]) / bridge["states"]["exit"])
        assert endpoint["robot"]["position"] == [original_x + 1, 0, 0]
    assert read_json(boundary_dir / item["states"]["entry"]) == original_entry

    compose_episode(session, recipe)
    result = read_json(recipe_path)
    selected = next(segment for segment in result["annotations"] if segment["id"] == segment_id)
    with h5py.File(session.paths.resolve(result["hdf5"]), "r") as stream:
        group = stream["data/demo_0"]
        start, end = selected["frames"]
        assert end - start == 40
        np.testing.assert_array_equal(group["action"][start:end, 0], np.arange(20, 60))
        entry = json.loads(group["state_json"][start])
        assert scene_objects(entry)["robot"]["root_link"]["pos"] == [original_x + 1, 0, 0]
        assert scene_objects(entry)[operated_object]["root_link"]["pos"][0] == 0
        if first_segment:
            assert start == 0


def test_query_cannot_use_a_trimmed_action_range(collected):
    session, _, _, run, _ = collected
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    recipe["segments"]["query"]["source"]["frames"][0] = 1
    with pytest.raises(ValueError, match="Query must use its entire"):
        compose_episode(session, recipe)


def test_composition_accepts_legacy_tasks_with_current_bridge_aabb(collected, material, monkeypatch):
    session, _, _, run, _ = collected
    run()
    backend = material[2]
    raw = {path: path.read_bytes() for path in session.paths.output_root.rglob("*.hdf5")}

    def bridge_with_aabb(session, scene, previous, entry, output):
        current = deepcopy(previous)
        for obj in scene_objects(current).values():
            obj["non_kin"]["AABB"] = {"aabb": [0, 0, 0, 1, 1, 1]}
        _assert_world_preserved(previous, current)
        return backend(session, scene, current, entry, output)

    monkeypatch.setattr("memory_bench.episodes.generate_bridges.generate_bridge", bridge_with_aabb)
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    bridges = {path: path.read_bytes() for path in session.paths.bridges_dir.rglob("*.hdf5")}
    result = read_json(compose_episode(session, recipe))
    assert result["total_frames"] == 14
    assert session.paths.resolve(result["hdf5"]).is_file()
    assert all(path.read_bytes() == data for path, data in (raw | bridges).items())


@pytest.mark.parametrize("field", ["run_id", "source_path", "instance_path"])
def test_rejects_changed_or_mixed_collection_demo(collected, material, field):
    session, _, _, run, _ = collected
    path, record = run()
    if field == "run_id":
        record["run_id"] = "demo_002"
    elif field == "source_path":
        record["segments"]["key_1"]["hdf5"] = "another_demo/key_01.hdf5"
    else:
        record["instance"] = "tro/instance_0002.json"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        build_episode(session, 1, session.composition["task_orders"][0])


def test_composition_uses_actual_kq_boundaries_without_collection_history(collected, material):
    session, _, _, run, _ = collected
    path, record = run(robot_positions={"key_1": 7, "query": 9})
    del record["transfers"]
    for key, position in (("key_1", 17), ("query", 23)):
        item = record["segments"][key]
        item["validation"] = {}
        for name in ("entry", "exit"):
            state_path = path.parent / item["states"][name]
            state = read_json(state_path)
            scene_objects(state)["toy"]["root_link"]["pos"][0] = position
            state_path.write_text(json.dumps(state))
        with h5py.File(session.paths.resolve(item["hdf5"]), "r+") as stream:
            for frame, value in enumerate(stream["data/demo_0/state_json"]):
                state = json.loads(value)
                scene_objects(state)["toy"]["root_link"]["pos"][0] = position
                stream["data/demo_0/state_json"][frame] = json.dumps(state)
    path.write_text(json.dumps(record))
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    result = read_json(compose_episode(session, recipe))
    query = read_json(session.paths.resolve(result["query_start"]))
    assert query["robot"]["position"] == [9, 0, 0]
    assert scene_objects(query)["toy"]["root_link"]["pos"][0] == 1


@pytest.mark.parametrize("field,value", [
    ("collection_complete", False), ("session_id", "other"), ("instance_id", 2),
    ("variant_id", "B"), ("run_id", "demo_002"), ("segment_id", "query"), ("frames", [0, 1]),
])
def test_collected_source_validation_belongs_to_composition(collected, material, field, value):
    session, _, _, run, _ = collected
    path, record = run()
    if field == "frames":
        record["segments"]["key_1"][field] = value
        path.write_text(json.dumps(record))
    else:
        with h5py.File(session.paths.resolve(record["segments"]["key_1"]["hdf5"]), "r+") as stream:
            stream["data"].attrs[field] = value
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    with pytest.raises(ValueError, match="collected source|Collected source"):
        compose_episode(session, recipe)
    assert not (session.paths.episodes_dir / "episode_00000000.hdf5").exists()


def test_old_query_event_metadata_does_not_reject_a_complete_demo(collected, material):
    session, _, _, run, _ = collected
    path, record = run()
    record["segments"]["query"]["validation"]["object_events"] = [{"forbidden": True}]
    path.write_text(json.dumps(record))
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    result = read_json(compose_episode(session, recipe))
    assert all("object_events" not in annotation for annotation in result["annotations"])


def test_raw_source_metadata_change_does_not_require_content_digest(collected, material):
    session, _, _, run, _ = collected
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    source = session.paths.resolve(recipe["segments"]["intervent_1"]["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        stream["data"].attrs["note"] = "metadata update"
    assert compose_episode(session, recipe).is_file()


def test_composition_rejects_robot_teleport_between_segments(collected, material):
    session, _, _, run, _ = collected
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    source = session.paths.resolve(recipe["segments"]["intervent_2"]["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        state = json.loads(stream["data/demo_0/state_json"][0])
        scene_objects(state)["robot"]["root_link"]["pos"][0] += 1
        stream["data/demo_0/state_json"][0] = json.dumps(state)
    with pytest.raises(ValueError, match="intervent_2 handoff"):
        compose_episode(session, recipe)
    assert not (session.paths.episodes_dir / "episode_00000000.hdf5").exists()


def test_composition_rejects_bridge_planned_in_different_inherited_world(collected, material):
    session, _, _, run, _ = collected
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    source = session.paths.resolve(recipe["segments"]["intervent_1"]["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        state = json.loads(stream["data/demo_0/state_json"][-1])
        scene_objects(state)["toy"]["root_link"]["pos"][0] += 1
        stream["data/demo_0/state_json"][-1] = json.dumps(state)
    with pytest.raises(ValueError, match="bridge_00 preconditions"):
        compose_episode(session, recipe)
    assert not (session.paths.episodes_dir / "episode_00000000.hdf5").exists()


@pytest.mark.parametrize("field,value", [("session_id", "other"), ("instance_id", 2), ("episode_id", "episode_00000001")])
def test_composition_rejects_mismatched_recipe_identity(collected, material, field, value):
    session, _, _, run, _ = collected
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    recipe[field] = value
    with pytest.raises(ValueError, match="Recipe identity"):
        compose_episode(session, recipe)


@pytest.mark.parametrize("changed_input", ["collection_manifest", "task_orders"])
def test_composition_uses_frozen_recipe(collected, material, changed_input):
    session, _, _, run, _ = collected
    path, record = run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    if changed_input == "collection_manifest":
        record["status"] = "in_progress"
        path.write_text(json.dumps(record))
    else:
        session.composition["task_orders"] = [["key_1", "intervent_1", "intervent_2", "query"]]
    assert compose_episode(session, recipe).is_file()


def test_composition_uses_recipe_source_after_relocation(collected):
    session, _, _, run, _ = collected
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    source = recipe["segments"]["key_1"]["source"]
    original = session.paths.resolve(source["hdf5"])
    relocated = session.paths.output_root / "relocated" / original.parent.name / original.name
    relocated.parent.mkdir(parents=True)
    original.rename(relocated)
    source["hdf5"] = str(relocated.relative_to(session.paths.output_root))

    assert compose_episode(session, recipe).is_file()


def test_transient_incidental_change_is_preserved_and_restored(collected, material):
    session, _, _, run, _ = collected
    _, record = run()
    # Keep boundaries intact but move an unrelated object in the middle of the Key.
    source = session.paths.resolve(record["segments"]["key_1"]["hdf5"])
    with h5py.File(source, "a") as stream:
        state = json.loads(stream["data/demo_0/state_json"][1])
        scene_objects(state)["hammer"]["root_link"]["pos"][0] = 99
        stream["data/demo_0/state_json"][1] = json.dumps(state)
    manifest = source.parent / "manifest.json"
    manifest.write_text(json.dumps(record))
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    result = read_json(compose_episode(session, recipe))
    annotation = next(item for item in result["annotations"] if item["id"] == "key_1")
    assert "hammer" in annotation["objects"]["write"]
    assert "hammer" not in recipe["segments"]["key_1"]["objects"]["write"]
    with h5py.File(session.paths.resolve(result["hdf5"]), "r") as stream:
        states = stream["data/demo_0/state_json"]
        middle = json.loads(states[annotation["frames"][0] + 1])
        restored = json.loads(states[annotation["frames"][1]])
        assert scene_objects(middle)["hammer"]["root_link"]["pos"][0] == 99
        assert scene_objects(restored)["hammer"]["root_link"]["pos"][0] == 0


def test_raw_reader_uses_real_terminal_and_ignores_rollback_branch(tmp_path):
    entry, exit_state = snapshot({"robot": 0, "toy": 0}), snapshot({"robot": 0, "toy": 1})
    source, terminal = tmp_path / "key_01.hdf5", tmp_path / "key_01_exit.json"
    canonical(source, entry, exit_state, "test", terminal=False)
    write_json(terminal, exit_state)
    with h5py.File(source, "a") as stream:
        stream["data/demo_0"].create_group("rollbacks").create_dataset("failed", data=[99])
    before = source.read_bytes()
    with FrameReader(source, terminal=terminal) as reader:
        assert reader.state(0) == entry["state"]
        assert reader.state(2) == exit_state["state"]
        assert reader.snapshot(2) == exit_state
    assert source.read_bytes() == before
    assert {path.name for path in tmp_path.iterdir()} == {"key_01.hdf5", "key_01_exit.json"}


def test_raw_serialized_reader_reuses_schema_and_decodes_without_output(tmp_path, monkeypatch):
    scene = snapshot({"robot": 0, "toy": 0})
    fake_og = ModuleType("omnigibson")
    fake_og.Environment = Mock(return_value=SimpleNamespace(scene=object()))
    fake_og.clear = Mock()
    fake_og.sim = SimpleNamespace(viewer_width=640, viewer_height=480,
                                  deserialize=Mock(return_value=([scene["state"]], 1)))
    wrapper = ModuleType("omnigibson.envs.data_wrapper")
    wrapper._add_recorded_non_kin_states_to_scene_file = lambda *_: None
    wrapper._align_scene_object_states_with_recorded_schema = lambda *_: None
    macros = ModuleType("omnigibson.macros")
    macros.gm = SimpleNamespace(unlocked=lambda: nullcontext())
    monkeypatch.setitem(sys.modules, "omnigibson", fake_og)
    monkeypatch.setitem(sys.modules, "omnigibson.envs.data_wrapper", wrapper)
    monkeypatch.setitem(sys.modules, "omnigibson.macros", macros)
    with RecordingContext() as context:
        for key in ("key_01", "query"):
            source = tmp_path / f"{key}.hdf5"
            canonical(source, scene, scene, key)
            with h5py.File(source, "a") as stream:
                group = stream["data/demo_0"]
                del group["state_json"]
                group.create_dataset("state", data=[[0.0], [0.0], [0.0]])
                group.create_dataset("state_size", data=[1, 1, 1])
            with FrameReader(source, context=context) as reader:
                assert reader.state(0) == scene["state"]
                assert reader.state(2) == scene["state"]
    assert fake_og.Environment.call_count == 1
    assert fake_og.clear.call_count == 1
    assert len(list(tmp_path.iterdir())) == 2

    with RecordingContext() as context:
        with FrameReader(tmp_path / "key_01.hdf5") as reader:
            metadata = reader.metadata
        first = {**metadata, "scene": snapshot({"robot": 0, "toy": 0})}
        second = {**metadata, "scene": snapshot({"robot": 0, "toy": 0})}
        scene_objects(first["scene"])["toy"]["non_kin"] = {"Open": {}, "Temperature": {}}
        scene_objects(second["scene"])["toy"]["non_kin"] = {"Temperature": {}, "Open": {}}
        context.prepare(first)
        context.prepare(second)
    assert fake_og.Environment.call_count == 3
    assert fake_og.clear.call_count == 3


def test_bridge_retry_reuses_completed_raw_recording(collected, material, monkeypatch):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    run()
    order = session.composition["task_orders"][0]

    def interrupted(session, scene, previous, entry, output):
        if output.stem == "bridge_01":
            raise RuntimeError("interrupted")
        return bridge(session, scene, previous, entry, output)

    monkeypatch.setattr("memory_bench.episodes.generate_bridges.generate_bridge", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        build_episode(session, 1, order)
    first = session.paths.bridges_dir / "episode_00000000/bridge_00.hdf5"
    saved = first.read_bytes()
    resumed = Mock(side_effect=bridge)
    monkeypatch.setattr("memory_bench.episodes.generate_bridges.generate_bridge", resumed)
    build_episode(session, 1, order)
    assert resumed.call_count == 2
    assert first.read_bytes() == saved


@pytest.mark.parametrize("boundary,object_name,expected", [
    ("entry", "toy", "Saved Bridge entry"),
    ("exit", "toy", "Bridge changed nonrobot world"),
    ("exit", "robot", "Bridge endpoint"),
])
def test_bridge_retry_rejects_incompatible_saved_boundaries(collected, material, monkeypatch, boundary, object_name, expected):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    run()
    order = session.composition["task_orders"][0]

    def interrupted(session, scene, previous, entry, output):
        if output.stem == "bridge_01":
            raise RuntimeError("interrupted")
        return bridge(session, scene, previous, entry, output)

    monkeypatch.setattr("memory_bench.episodes.generate_bridges.generate_bridge", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        build_episode(session, 1, order)
    path = session.paths.bridges_dir / f"episode_00000000/bridge_00_{boundary}.json"
    state = read_json(path)
    if object_name == "robot":
        state["robot"]["position"][0] += 1
    else:
        scene_objects(state)[object_name]["root_link"]["pos"][0] += 1
    path.write_text(json.dumps(state))
    resumed = Mock(side_effect=bridge)
    monkeypatch.setattr("memory_bench.episodes.generate_bridges.generate_bridge", resumed)
    with pytest.raises(ValueError, match=expected):
        build_episode(session, 1, order)
    resumed.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("session_id", "other"), ("instance_id", 2), ("variant_id", "other"),
    ("run_id", "demo_002"), ("segment_id", "key_1"),
])
def test_composition_rejects_mismatched_recording_identity(collected, material, field, value):
    session, _, _, run, _ = collected
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    source = session.paths.resolve(recipe["segments"]["query"]["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        stream["data"].attrs[field] = value

    with pytest.raises(ValueError, match=f"Collected source {field} differs"):
        compose_episode(session, recipe)


def test_terminal_snapshot_carries_incidental_changes(collected, material):
    session, _, _, run, _ = collected
    path, _ = run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    exit_path = path.parent / "query_exit.json"
    exit_state = read_json(exit_path)
    scene_objects(exit_state)["toy"]["root_link"]["pos"][0] = 99
    exit_path.write_text(json.dumps(exit_state))
    result = read_json(compose_episode(session, recipe))
    assert "toy" in result["annotations"][-1]["objects"]["write"]
    with h5py.File(session.paths.resolve(result["hdf5"]), "r") as stream:
        final = json.loads(stream["data/demo_0/state_json"][-1])
        assert scene_objects(final)["toy"]["root_link"]["pos"][0] == 99


def test_builder_and_composer_carry_incidental_task_exit_into_bridges(collected):
    session, _, _, run, _ = collected
    path, _ = run()
    exit_path = path.parent / "key_01_exit.json"
    exit_state = read_json(exit_path)
    scene_objects(exit_state)["toy"]["root_link"]["pos"][0] = 99
    exit_path.write_text(json.dumps(exit_state))
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    bridge = recipe["bridges"]["bridge_01"]
    entry = read_json(session.paths.resolve(bridge["states"]["directory"]) / bridge["states"]["entry"])
    assert scene_objects(entry)["toy"]["root_link"]["pos"][0] == 99
    result = read_json(compose_episode(session, recipe))
    with h5py.File(session.paths.resolve(result["hdf5"]), "r") as stream:
        final = json.loads(stream["data/demo_0/state_json"][-1])
        assert scene_objects(final)["toy"]["root_link"]["pos"][0] == 99


@pytest.mark.parametrize("role", ["intervent_task", "bridge_task", "key_task", "query_task"])
def test_relaxed_writes_still_protect_memory(role):
    baseline = snapshot({"robot": 0, "baking_soda": 0, "cabinet": 0, "toy": 0})
    segment = {
        "identity": {"id": "segment"}, "task": {"role": role},
        "interface": {"robot_name": "robot"}, "objects": {"write": ["baking_soda"]},
    }
    changed = deepcopy(baseline)
    scene_objects(changed)["cabinet"]["root_link"]["pos"][0] = 1
    with pytest.raises(ValueError, match="memory"):
        check_frame(segment, changed, baseline, {"baking_soda", "cabinet"})
    changed = deepcopy(baseline)
    scene_objects(changed)["baking_soda"]["root_link"]["pos"][0] = 1
    if role in ("intervent_task", "bridge_task"):
        with pytest.raises(ValueError, match="memory"):
            check_frame(segment, changed, baseline, {"baking_soda", "cabinet"})
    else:
        assert check_frame(segment, changed, baseline, {"baking_soda", "cabinet"}) == {"baking_soda"}
    changed = deepcopy(baseline)
    scene_objects(changed)["toy"]["root_link"]["pos"][0] = 1
    if role == "bridge_task":
        with pytest.raises(ValueError, match="Undeclared state changes"):
            check_frame(segment, changed, baseline, {"baking_soda", "cabinet"})
    else:
        assert check_frame(segment, changed, baseline, {"baking_soda", "cabinet"}) == {"toy"}


@pytest.mark.parametrize("change", ["add_object", "remove_object", "system"])
def test_relaxed_writes_still_reject_topology_changes(change):
    baseline = snapshot({"robot": 0, "toy": 0})
    segment = {
        "identity": {"id": "query"}, "task": {"role": "query_task"},
        "interface": {"robot_name": "robot"}, "objects": {"write": []},
    }
    changed = deepcopy(baseline)
    if change == "add_object":
        scene_objects(changed)["new"] = deepcopy(scene_objects(changed)["toy"])
    elif change == "remove_object":
        del scene_objects(changed)["toy"]
    else:
        changed["state"]["registry"]["system_registry"]["water"] = {}
    with pytest.raises(ValueError, match="topology or particle systems"):
        check_frame(segment, changed, baseline, set())


@pytest.mark.parametrize("serialized", [False, True])
def test_preparation_only_freezes_raw_sources(collected, monkeypatch, serialized):
    session, _, _, _, inventory_path = collected
    root = session.paths.session_dir
    (root / "intervent_assignments.json").unlink()
    variants = (root / "variant_assignments.json").read_bytes()
    inventory = read_json(inventory_path)
    if serialized:
        for task in inventory["tasks"].values():
            for candidate in task["candidates"]:
                with h5py.File(session.paths.resolve(candidate["hdf5"]), "a") as stream:
                    group = stream["data"][f"demo_{candidate['demo']}"]
                    count = len(group["state_json"])
                    del group["state_json"]
                    # Preparation must not try to decode these opaque serialized states.
                    group.create_dataset("state", data=[[0.0]] * count)
                    group.create_dataset("state_size", data=[1] * count)
    sources = {item["hdf5"]: session.paths.resolve(item["hdf5"]).read_bytes()
               for task in inventory["tasks"].values() for item in task["candidates"]}

    original_import = builtins.__import__

    def import_without_simulator(name, *args, **kwargs):
        if name == "omnigibson" or name.startswith("omnigibson."):
            raise AssertionError("Intervention preparation must not import the simulator")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_simulator)
    assigned = prepare_intervents(session, inventory)
    assert all(set(source) == {"hdf5", "demo"} for refs in assigned.values() for source in refs.values())
    frozen = (root / "intervent_assignments.json").read_bytes()
    for task in inventory["tasks"].values():
        task["candidates"] = []
    with pytest.raises(FileExistsError, match="Intervention assignments already exist"):
        prepare_intervents(session, inventory)
    assert (root / "intervent_assignments.json").read_bytes() == frozen
    assert (root / "variant_assignments.json").read_bytes() == variants
    assert all(session.paths.resolve(path).read_bytes() == content for path, content in sources.items())
    assert not session.paths.resolve("segments").exists()
    assert not list(session.paths.output_root.rglob("*normalized*"))
    assert not session.paths.reports_dir.exists()


def test_preparation_rejects_incompatible_asset_metadata(collected, capsys):
    session, _, _, _, inventory_path = collected
    root = session.paths.session_dir
    (root / "intervent_assignments.json").unlink()
    variants = read_json(root / "variant_assignments.json")
    variants["instances"].append({**variants["instances"][0], "instance_id": 2})
    (root / "variant_assignments.json").write_text(json.dumps(variants))
    inventory = read_json(inventory_path)
    candidate = inventory["tasks"]["intervent_1"]["candidates"][0]
    source = session.paths.resolve(candidate["hdf5"])
    incompatible = source.with_name("incompatible.hdf5")
    incompatible.write_bytes(source.read_bytes())
    name = candidate["used"][0]
    with h5py.File(incompatible, "a") as stream:
        scene = json.loads(stream["data"].attrs["scene_file"])
        scene["objects_info"]["init_info"][name]["args"]["model"] = "different_model"
        stream["data"].attrs["scene_file"] = json.dumps(scene)
    inventory["tasks"]["intervent_1"]["candidates"].append({**candidate, "hdf5": str(incompatible)})

    with pytest.raises(ValueError, match="Only 1/2 compatible sources"):
        prepare_intervents(session, inventory)

    assert f"Conflicting asset {name}: model" in capsys.readouterr().out
    assert not session.paths.reports_dir.exists()
    assert not (root / "intervent_assignments.json").exists()


def test_serialized_reader_carries_filtered_objects_and_can_rewind(tmp_path, monkeypatch):
    initial = snapshot({"robot": 0, "toy": 0})
    source = tmp_path / "intervent_01.hdf5"
    canonical(source, initial, initial, "test")
    with h5py.File(source, "a") as stream:
        group = stream["data/demo_0"]
        del group["state_json"]
        group.create_dataset("state", data=[[0.0], [1.0], [2.0]])
        group.create_dataset("state_size", data=[1, 1, 1])

    def deserialize(flat):
        index = int(flat[0])
        objects = (
            {"toy": scene_objects(snapshot({"robot": 0, "toy": 7}))["toy"]}
            if index == 1 else {"robot": scene_objects(initial)["robot"]}
        )
        return [{"registry": {"object_registry": objects, "system_registry": {}}}], 1

    monkeypatch.setitem(sys.modules, "omnigibson", SimpleNamespace(sim=SimpleNamespace(deserialize=deserialize)))
    context = SimpleNamespace(prepare=lambda metadata: None)
    with FrameReader(source, context=context) as reader:
        assert scene_objects(reader.state(2))["toy"]["root_link"]["pos"][0] == 7
        assert scene_objects(reader.state(0))["toy"]["root_link"]["pos"][0] == 0
        assert scene_objects(reader.state(1))["toy"]["root_link"]["pos"][0] == 7


def test_query_snapshot_binds_agent_to_actual_episode_robot(collected):
    session, _, _, run, _ = collected
    assignments_path = session.paths.session_dir / "variant_assignments.json"
    assignments = read_json(assignments_path)
    assignments["instances"][0]["bindings"]["query"]["agent.n.01_1"] = "cached_sampling_robot"
    assignments_path.write_text(json.dumps(assignments))
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    result = read_json(compose_episode(session, recipe))
    restored = read_json(session.paths.resolve(result["query_start"]))
    assert restored["metadata"]["task"]["inst_to_name"]["agent.n.01_1"] == restored["robot"]["name"]
    assert result["annotations"][-1]["bindings"]["agent.n.01_1"] == restored["robot"]["name"]
