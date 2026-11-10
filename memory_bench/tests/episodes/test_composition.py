import json
from copy import deepcopy
from unittest.mock import Mock

import h5py
import numpy as np
import pytest

from memory_bench.episodes.build_episodes import build_bridges, build_episode
from memory_bench.episodes.compose_episodes import compose_episode, compose_trajectory
from memory_bench.episodes.generate_bridges import _assert_world_preserved
from memory_bench.episodes.replay_episodes import validate_annotations
from memory_bench.tests.helpers import make_scene_snapshot, write_recording
from memory_bench.utils.recording.reader import FrameReader
from memory_bench.utils.recording.segments import validate_segments
from memory_bench.utils.scene.state import read_json, remap_robot_name, scene_objects


def _segment(recipe, segment_id):
    return next(item for item in recipe["segments"] if item["id"] == segment_id)


@pytest.mark.parametrize("demo_id", [1, 2])
def test_selects_only_requested_demo(collected_episode, demo_id):
    session, _, _, run, _ = collected_episode
    runs = {number: run(number)[0] for number in (1, 2)}
    session.composition["demo_id"] = demo_id
    path = build_episode(session, 1, session.composition["task_orders"][0])
    recipe = read_json(path)
    for key in [*session.key_ids, session.query_id]:
        source = session.paths.resolve(_segment(recipe, key)["source"]["hdf5"])
        assert source.parent == runs[demo_id].parent
    for manifest in runs.values():
        manifest.unlink()
    session.composition["demo_id"] = 3 - demo_id
    compose_episode(session, recipe)
    result = read_json(path)
    assert [(item["id"], item["source"]) for item in result["segments"]] == [
        (item["id"], item["source"]) for item in recipe["segments"]
    ]
    assert "collection_manifest" not in result
    assert "collection_demo" not in result


@pytest.mark.parametrize("terminal", [True, False])
def test_intervent_retains_only_actions_with_recorded_end_states(collected_episode, episode_sources, terminal):
    session, _, _, run, _ = collected_episode
    run()
    source = session.paths.resolve(episode_sources[3]["intervent_1"]["source"]["hdf5"])
    with h5py.File(source, "r+") as stream:
        group = stream["data/demo_0"]
        if not terminal:
            states = group["state_json"].asstr()[:]
            del group["state_json"]
            group.create_dataset("state_json", data=[states[0], states[-1]], dtype=h5py.string_dtype())
        group["action"][0] = 0.5
        group["action"][1] = 0.75
        final = json.loads(group["state_json"][-1])
    original = source.read_bytes()

    path = build_episode(session, 1, session.composition["task_orders"][0])
    recipe = read_json(path)
    end = 2 if terminal else 1
    assert _segment(recipe, "intervent_1")["source"]["frames"] == [0, end]
    for key in [*session.key_ids, session.query_id, "intervent_2"]:
        assert _segment(recipe, key)["source"]["frames"] == [0, 2]
    bridge = _segment(recipe, "bridge_00")
    entry = read_json(session.paths.resolve(bridge["states"]["directory"]) / bridge["states"]["entry"])
    assert scene_objects(entry)["toy"] == scene_objects(final)["toy"]

    compose_episode(session, recipe)
    result = read_json(path)
    segment = _segment(result, "intervent_1")
    with h5py.File(session.paths.resolve(result["output"]["hdf5"]), "r") as stream:
        group = stream["data/demo_0"]
        start, stop = segment["frames"]
        assert stop - start == end
        assert group["action"][start:stop, 0].tolist() == ([0.5, 0.75] if terminal else [0.5])
        assert scene_objects(json.loads(group["state_json"][stop]))["toy"] == scene_objects(final)["toy"]
        assert len(group["state_json"]) == len(group["action"]) + 1
    assert source.read_bytes() == original


def test_missing_first_demo_does_not_fall_back(collected_episode):
    session, _, _, run, _ = collected_episode
    run(2)
    with pytest.raises(FileNotFoundError):
        build_episode(session, 1, session.composition["task_orders"][0])


def test_incomplete_first_demo_does_not_fall_back(collected_episode):
    session, _, _, run, _ = collected_episode
    run(1, "in_progress")
    run(2)
    with pytest.raises(ValueError, match="incomplete"):
        build_episode(session, 1, session.composition["task_orders"][0])


def test_bridges_and_composition_are_independent_and_query_is_restorable(collected_episode, episode_sources, monkeypatch):
    session, _, _, run, _ = collected_episode
    manifest_path, _ = run(1)
    run(2)
    raw = {path: path.read_bytes() for path in session.paths.output_root.rglob("*.hdf5")}
    path = build_episode(session, 1, session.composition["task_orders"][0])
    recipe = read_json(path)
    assert session.paths.resolve(_segment(recipe, "key_1")["source"]["hdf5"]).parent == manifest_path.parent
    assert sum(item["role"] == "bridge_task" for item in recipe["segments"]) == 3
    assert list(recipe) == ["session_id", "episode_id", "instance_id", "instance", "segments"]
    for segment in recipe["segments"]:
        assert set(segment) <= {"id", "role", "activity", "instruction", "source", "states", "objects"}
        assert set(segment["source"]) == {"hdf5", "demo", "frames"}
        assert set(segment["objects"]) == {"used", "read", "write"}
        if segment["role"] == "bridge_task":
            assert segment["instruction"] == ""
    assert not session.paths.episodes_dir.exists()
    assert not session.paths.resolve("segments").exists()
    assert not list(session.paths.output_root.rglob("*normalized*"))
    assert all(source.read_bytes() == data for source, data in raw.items())
    monkeypatch.setattr("memory_bench.episodes.generate_bridges.generate_bridge", Mock(side_effect=AssertionError))
    compose_episode(session, recipe)
    result = read_json(path)
    assert list(result) == ["session_id", "episode_id", "instance_id", "instance", "segments", "output"]
    assert list(result["output"]) == ["hdf5", "replay_scene", "query_start", "query_start_frame", "fps", "total_frames"]
    assert result["output"]["total_frames"] == 14
    restored = read_json(session.paths.resolve(result["output"]["query_start"]))
    with h5py.File(session.paths.resolve(result["output"]["hdf5"]), "r") as stream:
        assert restored["state"] == json.loads(stream["data/demo_0/state_json"][result["output"]["query_start_frame"]])
        assert len(stream["data/demo_0/state_json"]) == len(stream["data/demo_0/action"]) + 1
        for segment in result["segments"]:
            if segment["role"] == "bridge_task":
                start, end = segment["frames"]
                assert stream["data/demo_0/instruction"].asstr()[start:end].tolist() == [""] * (end - start)
    objects = scene_objects(restored)
    assert objects["item"]["root_link"]["pos"][0] == 3
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
@pytest.mark.parametrize("trim_entry", [False, True])
def test_selected_entry_survives_bridge_build_and_composition(
    collected_episode, episode_sources, segment_id, first_segment, trim_entry
):
    session, _, _, run, _ = collected_episode
    _, record = run()
    item = record["segments"][segment_id] if segment_id == "key_1" else episode_sources[3][segment_id]
    source = session.paths.resolve(item["hdf5"] if segment_id == "key_1" else item["source"]["hdf5"])
    boundary_dir = session.paths.resolve(item["states"]["directory"])
    original_entry = read_json(boundary_dir / item["states"]["entry"])
    scene_objects(original_entry)["robot"].update(joint_vel=[0.0] * 28, ag_obj_constraint_params={})
    (boundary_dir / item["states"]["entry"]).write_text(json.dumps(original_entry))
    original_x = original_entry["robot"]["position"][0]
    operated_object = "item" if segment_id == "key_1" else "toy"
    destination = 3 if segment_id == "key_1" else 1
    states = []
    for frame in range(61):
        state = deepcopy(original_entry["state"])
        objects = scene_objects(state)
        objects["robot"].update(joint_vel=[0.0] * 28, ag_obj_constraint_params={})
        objects["robot"]["root_link"]["pos"][0] = original_x + min(frame, 20) / 20
        if 0 < frame < 20:
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
    recipe_path = build_episode(session, 1, order, trim_entry=True) if trim_entry else build_episode(session, 1, order)
    recipe = read_json(recipe_path)
    expected_start = 20 if trim_entry else 0
    expected_x = original_x + 1 if trim_entry else original_x
    assert _segment(recipe, segment_id)["source"]["frames"] == [expected_start, 60]
    assert _segment(recipe, "query")["source"]["frames"] == [0, 2]
    if not first_segment:
        bridge = _segment(recipe, "bridge_00")
        endpoint = read_json(session.paths.resolve(bridge["states"]["directory"]) / bridge["states"]["exit"])
        assert endpoint["robot"]["position"] == [expected_x, 0, 0]
    assert read_json(boundary_dir / item["states"]["entry"]) == original_entry

    compose_episode(session, recipe)
    result = read_json(recipe_path)
    selected = next(segment for segment in result["segments"] if segment["id"] == segment_id)
    with h5py.File(session.paths.resolve(result["output"]["hdf5"]), "r") as stream:
        group = stream["data/demo_0"]
        start, end = selected["frames"]
        assert end - start == 60 - expected_start
        np.testing.assert_array_equal(group["action"][start:end, 0], np.arange(expected_start, 60))
        entry = json.loads(group["state_json"][start])
        assert scene_objects(entry)["robot"]["root_link"]["pos"] == [expected_x, 0, 0]
        assert scene_objects(entry)[operated_object]["root_link"]["pos"][0] == 0
        if first_segment:
            assert start == 0


def test_query_cannot_use_a_trimmed_action_range(collected_episode):
    session, _, _, run, _ = collected_episode
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    _segment(recipe, "query")["source"]["frames"][0] = 1
    with pytest.raises(ValueError, match="Query must use its entire"):
        compose_episode(session, recipe)


@pytest.mark.parametrize("defect", ["duplicate_id", "source_interface"])
def test_composition_checks_compact_recipe_sources(collected_episode, defect):
    session, _, _, run, _ = collected_episode
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    if defect == "duplicate_id":
        recipe["segments"].append(deepcopy(recipe["segments"][0]))
        message = "Duplicate recipe segment"
    else:
        source = session.paths.resolve(_segment(recipe, "intervent_1")["source"]["hdf5"])
        with h5py.File(source, "a") as stream:
            config = json.loads(stream["data"].attrs["config"])
            config["robots"][0]["controller_config"] = {"base": {"name": "incompatible"}}
            stream["data"].attrs["config"] = json.dumps(config)
        message = "Robot/controller/action/frequency mismatch"
    with pytest.raises(ValueError, match=message):
        compose_episode(session, recipe)
    assert not session.paths.episodes_dir.exists()


def test_composition_accepts_legacy_tasks_with_current_bridge_aabb(collected_episode, episode_sources, monkeypatch):
    session, _, _, run, _ = collected_episode
    run()
    backend = episode_sources[2]
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
    assert result["output"]["total_frames"] == 14
    assert session.paths.resolve(result["output"]["hdf5"]).is_file()
    assert all(path.read_bytes() == data for path, data in (raw | bridges).items())


@pytest.mark.parametrize("field", ["run_id", "source_path", "instance_path"])
def test_rejects_changed_or_mixed_collection_demo(collected_episode, episode_sources, field):
    session, _, _, run, _ = collected_episode
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


def test_composition_uses_actual_kq_boundaries_without_collection_history(collected_episode, episode_sources):
    session, _, _, run, _ = collected_episode
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
    query = read_json(session.paths.resolve(result["output"]["query_start"]))
    assert query["robot"]["position"] == [9, 0, 0]
    assert scene_objects(query)["toy"]["root_link"]["pos"][0] == 1


@pytest.mark.parametrize("field,value", [
    ("collection_complete", False), ("session_id", "other"), ("instance_id", 2),
    ("variant_id", "B"), ("run_id", "demo_002"), ("segment_id", "query"), ("frames", [0, 1]),
])
def test_collected_source_validation_belongs_to_composition(collected_episode, episode_sources, field, value):
    session, _, _, run, _ = collected_episode
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


def test_composition_rejects_robot_teleport_between_segments(collected_episode, episode_sources):
    session, _, _, run, _ = collected_episode
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    source = session.paths.resolve(_segment(recipe, "intervent_2")["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        state = json.loads(stream["data/demo_0/state_json"][0])
        scene_objects(state)["robot"]["root_link"]["pos"][0] += 1
        stream["data/demo_0/state_json"][0] = json.dumps(state)
    with pytest.raises(ValueError, match="intervent_2 handoff"):
        compose_episode(session, recipe)
    assert not (session.paths.episodes_dir / "episode_00000000.hdf5").exists()


def test_composition_rejects_bridge_planned_in_different_inherited_world(collected_episode, episode_sources):
    session, _, _, run, _ = collected_episode
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    source = session.paths.resolve(_segment(recipe, "intervent_1")["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        state = json.loads(stream["data/demo_0/state_json"][-1])
        scene_objects(state)["toy"]["root_link"]["pos"][0] += 1
        stream["data/demo_0/state_json"][-1] = json.dumps(state)
    with pytest.raises(ValueError, match="bridge_00 preconditions"):
        compose_episode(session, recipe)
    assert not (session.paths.episodes_dir / "episode_00000000.hdf5").exists()


@pytest.mark.parametrize("field,value", [("session_id", "other"), ("instance_id", 2), ("episode_id", "episode_00000001")])
def test_composition_rejects_mismatched_recipe_identity(collected_episode, episode_sources, field, value):
    session, _, _, run, _ = collected_episode
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    recipe[field] = value
    with pytest.raises(ValueError, match="Recipe identity"):
        compose_episode(session, recipe)


@pytest.mark.parametrize("changed_input", ["collection_manifest", "task_orders"])
def test_composition_uses_frozen_recipe(collected_episode, episode_sources, changed_input):
    session, _, _, run, _ = collected_episode
    path, record = run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    if changed_input == "collection_manifest":
        record["status"] = "in_progress"
        path.write_text(json.dumps(record))
    else:
        session.composition["task_orders"] = [["key_1", "intervent_1", "intervent_2", "query"]]
    assert compose_episode(session, recipe).is_file()


def test_composition_uses_recipe_source_after_relocation(collected_episode):
    session, _, _, run, _ = collected_episode
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    source = _segment(recipe, "key_1")["source"]
    original = session.paths.resolve(source["hdf5"])
    relocated = session.paths.output_root / "relocated" / original.parent.name / original.name
    relocated.parent.mkdir(parents=True)
    original.rename(relocated)
    source["hdf5"] = str(relocated.relative_to(session.paths.output_root))

    assert compose_episode(session, recipe).is_file()


def test_transient_incidental_change_is_preserved_and_restored(collected_episode, episode_sources):
    session, _, _, run, _ = collected_episode
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
    annotation = next(item for item in result["segments"] if item["id"] == "key_1")
    assert "hammer" in annotation["objects"]["write"]
    assert "hammer" not in _segment(recipe, "key_1")["objects"]["write"]
    with h5py.File(session.paths.resolve(result["output"]["hdf5"]), "r") as stream:
        states = stream["data/demo_0/state_json"]
        middle = json.loads(states[annotation["frames"][0] + 1])
        restored = json.loads(states[annotation["frames"][1]])
        assert scene_objects(middle)["hammer"]["root_link"]["pos"][0] == 99
        assert scene_objects(restored)["hammer"]["root_link"]["pos"][0] == 0


def test_bridge_retry_reuses_completed_raw_recording(collected_episode, episode_sources, monkeypatch):
    session, _, _, run, _ = collected_episode
    _, _, bridge, _ = episode_sources
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
def test_bridge_retry_rejects_incompatible_saved_boundaries(collected_episode, episode_sources, monkeypatch, boundary, object_name, expected):
    session, _, _, run, _ = collected_episode
    _, _, bridge, _ = episode_sources
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
def test_composition_rejects_mismatched_recording_identity(collected_episode, episode_sources, field, value):
    session, _, _, run, _ = collected_episode
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    source = session.paths.resolve(_segment(recipe, "query")["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        stream["data"].attrs[field] = value

    with pytest.raises(ValueError, match=f"Collected source {field} differs"):
        compose_episode(session, recipe)


def test_terminal_snapshot_carries_incidental_changes(collected_episode, episode_sources):
    session, _, _, run, _ = collected_episode
    path, _ = run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    exit_path = path.parent / "query_exit.json"
    exit_state = read_json(exit_path)
    scene_objects(exit_state)["toy"]["root_link"]["pos"][0] = 99
    exit_path.write_text(json.dumps(exit_state))
    result = read_json(compose_episode(session, recipe))
    assert "toy" in result["segments"][-1]["objects"]["write"]
    with h5py.File(session.paths.resolve(result["output"]["hdf5"]), "r") as stream:
        final = json.loads(stream["data/demo_0/state_json"][-1])
        assert scene_objects(final)["toy"]["root_link"]["pos"][0] == 99


def test_builder_and_composer_carry_incidental_task_exit_into_bridges(collected_episode):
    session, _, _, run, _ = collected_episode
    path, _ = run()
    exit_path = path.parent / "key_01_exit.json"
    exit_state = read_json(exit_path)
    scene_objects(exit_state)["toy"]["root_link"]["pos"][0] = 99
    exit_path.write_text(json.dumps(exit_state))
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    bridge = _segment(recipe, "bridge_01")
    entry = read_json(session.paths.resolve(bridge["states"]["directory"]) / bridge["states"]["entry"])
    assert scene_objects(entry)["toy"]["root_link"]["pos"][0] == 99
    result = read_json(compose_episode(session, recipe))
    with h5py.File(session.paths.resolve(result["output"]["hdf5"]), "r") as stream:
        final = json.loads(stream["data/demo_0/state_json"][-1])
        assert scene_objects(final)["toy"]["root_link"]["pos"][0] == 99


def test_query_snapshot_binds_agent_to_actual_episode_robot(collected_episode):
    session, _, _, run, _ = collected_episode
    assignments_path = session.paths.session_dir / "variant_assignments.json"
    assignments = read_json(assignments_path)
    assignments["instances"][0]["bindings"]["query"]["agent.n.01_1"] = "cached_sampling_robot"
    assignments_path.write_text(json.dumps(assignments))
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0]))
    result = read_json(compose_episode(session, recipe))
    restored = read_json(session.paths.resolve(result["output"]["query_start"]))
    assert restored["metadata"]["task"]["inst_to_name"]["agent.n.01_1"] == restored["robot"]["name"]
    assert "bindings" not in result["segments"][-1]


@pytest.fixture
def partial_intervent(episode_sources):
    session, _, _, _ = episode_sources
    entry = make_scene_snapshot({"robot": 0, "toy": 0, "outside": 0})
    exit_state = make_scene_snapshot({"robot": 1, "toy": 1, "outside": 0})
    source = session.paths.output_root / "partial_intervent.hdf5"
    interface = write_recording(source, entry, exit_state, "turning_on_radio")
    segment = {
        "identity": {"id": "intervent_1", "instance_id": 1},
        "task": {**session.segments["intervent_1"], "instruction": "Turn on the radio."},
        "source": {"hdf5": source.name, "demo": 0, "frames": [0, 2]},
        "objects": {"used": ["toy"], "read": ["toy"], "write": ["toy"], "memory_bindings": {}},
        "boundaries": {"entry": entry, "exit": exit_state},
        "interface": interface,
    }
    scene = remap_robot_name(make_scene_snapshot({"robot": 0, "toy": 0}), "robot", "episode_robot")
    return session, scene, segment, source


def test_consecutive_intervents_preserve_inherited_world(episode_sources, monkeypatch):
    session, pairing, backend, _ = episode_sources
    order = ["intervent_1", "intervent_2"]
    references = {key: pairing["segments"][key] for key in order}
    segments = validate_segments(references)
    scene = read_json(session.paths.resolve(segments[order[0]]["states"]["entry"]))

    def inherited_world_bridge(session, scene_file, previous_exit, next_entry, output_path):
        for world in (scene_file, previous_exit):
            objects = scene_objects(world)
            assert objects["toy"]["root_link"]["pos"][0] == 1
            assert objects["hammer"]["root_link"]["pos"][0] == 0
        return backend(session, scene_file, previous_exit, next_entry, output_path)

    monkeypatch.setattr("memory_bench.episodes.generate_bridges.generate_bridge", inherited_world_bridge)
    sequence, bridges = build_bridges(session, scene, order, segments, "episode_00000000", 1)
    recipe = compose_trajectory(session, scene, sequence, set(), "episode_00000000", {"bindings": {}})
    validate_annotations({
        "segments": recipe["segments"], "total_frames": recipe["output"]["total_frames"], "memory_spans": [],
    })
    assert [item["id"] for item in recipe["segments"] if item["role"] != "bridge_task"] == order
    assert list(bridges) == ["bridge_00"]
    assert recipe["output"]["total_frames"] == 6
    assert [(item["id"], item["frames"]) for item in recipe["segments"]] == [
        ("intervent_1", [0, 2]),
        ("bridge_00", [2, 4]),
        ("intervent_2", [4, 6]),
    ]
    with h5py.File(session.paths.resolve(recipe["output"]["hdf5"]), "r") as stream:
        group = stream["data/demo_0"]
        states = [scene_objects(json.loads(value)) for value in group["state_json"]]
        assert len(states) == len(group["action"]) + 1 == 7
        assert [state["toy"]["root_link"]["pos"][0] for state in states] == [0, 0, 1, 1, 1, 1, 1]
        assert [state["hammer"]["root_link"]["pos"][0] for state in states] == [0, 0, 0, 0, 0, 0, 2]
        assert states[-1]["robot"]["root_link"]["pos"][0] == 3
        assert group["instruction"].asstr()[:].tolist() == ["intervent_1"] * 2 + [""] * 2 + ["intervent_2"] * 2


def test_partial_episode_keeps_original_source_schema_and_maps_robot(partial_intervent):
    session, scene, segment, source = partial_intervent
    original = source.read_bytes()
    sequence, _ = build_bridges(session, scene, ["intervent_1"], {"intervent_1": segment}, "partial", 1)
    result = compose_trajectory(session, scene, sequence, set(), "partial", {"bindings": {}})
    with h5py.File(session.paths.resolve(result["output"]["hdf5"]), "r") as stream:
        replay_scene = json.loads(stream["data"].attrs["scene_file"])
        assert set(replay_scene["objects_info"]["init_info"]) == {"episode_robot", "toy"}
        for value in stream["data/demo_0/state_json"]:
            assert set(scene_objects(json.loads(value))) == {"episode_robot", "toy"}
        final = scene_objects(json.loads(stream["data/demo_0/state_json"][-1]))
        assert final["toy"]["root_link"]["pos"][0] == 1
        assert final["episode_robot"]["root_link"]["pos"][0] == 1
    assert source.read_bytes() == original
    with FrameReader(source) as reader:
        assert "outside" in scene_objects(reader.snapshot(2))


@pytest.mark.parametrize("change", ["declared_write", "observed_write", "missing_initializer"])
def test_builder_and_composer_reject_writes_outside_partial_scene(partial_intervent, change):
    session, scene, segment, source = partial_intervent
    if change == "declared_write":
        segment["objects"]["write"].append("outside")
    elif change == "observed_write":
        scene_objects(segment["boundaries"]["exit"])["outside"]["root_link"]["pos"][0] = 1
        with h5py.File(source, "a") as stream:
            stream["data/demo_0/state_json"][2] = json.dumps(segment["boundaries"]["exit"]["state"])
    else:
        # A state entry alone cannot make a missing initializer replayable.
        del scene["objects_info"]["init_info"]["toy"]
    with pytest.raises(ValueError, match="outside the prepared scene"):
        build_bridges(session, scene, ["intervent_1"], {"intervent_1": segment}, "partial", 1)
    with pytest.raises(ValueError, match="outside the prepared scene"):
        compose_trajectory(session, scene, [segment], set(), "partial", {"bindings": {}})
    assert not (session.paths.episodes_dir / "partial.hdf5").exists()


def test_composer_rejects_transient_changes_to_cropped_objects(partial_intervent):
    session, scene, segment, source = partial_intervent
    with h5py.File(source, "a") as stream:
        middle = json.loads(stream["data/demo_0/state_json"][1])
        scene_objects(middle)["outside"]["non_kin"]["ToggledOn"] = {"toggled_on": True}
        stream["data/demo_0/state_json"][1] = json.dumps(middle)
    sequence, _ = build_bridges(session, scene, ["intervent_1"], {"intervent_1": segment}, "partial", 1)
    with pytest.raises(ValueError, match="outside the prepared scene.*outside"):
        compose_trajectory(session, scene, sequence, set(), "partial", {"bindings": {}})
    assert not (session.paths.episodes_dir / "partial.hdf5").exists()


@pytest.mark.parametrize(
    ("change", "error"),
    [("topology", "topology or particle systems"), ("system", "topology or particle systems"),
     ("protected", "altered protected memory")],
)
def test_partial_composer_checks_original_recording_world(partial_intervent, change, error):
    session, scene, segment, source = partial_intervent
    with h5py.File(source, "a") as stream:
        middle = json.loads(stream["data/demo_0/state_json"][1])
        if change == "topology":
            del scene_objects(middle)["outside"]
        elif change == "system":
            middle["registry"]["system_registry"]["water"] = {}
        else:
            scene_objects(middle)["outside"]["root_link"]["pos"][0] = 1
        stream["data/demo_0/state_json"][1] = json.dumps(middle)
    with pytest.raises(ValueError, match=error):
        compose_trajectory(session, scene, [segment], {"outside"}, "partial", {"bindings": {}})


def test_multi_key_and_shared_state_dependencies(episode_sources):
    session, _, _, _ = episode_sources
    session.config = deepcopy(session.config)
    session.segments["key_2"] = {
        "role": "key_task",
        "activity": "update",
        "room_id": "kitchen_0",
        "depends_on": ["key_1"],
    }
    session.segments["query"]["depends_on"] = ["key_1", "key_2"]
    session.config["benchmark"]["level"] = 2
    session.config["composition"]["task_orders"] = [["intervent_1", "key_1", "key_2", "intervent_2", "query"]]
    session.validate()
    with pytest.raises(ValueError, match="Unmet dependency"):
        session.validate_order(["intervent_1", "key_2", "key_1", "intervent_2", "query"])
    session.segments["intervent_2"]["room_id"] = "living_room_0"
    with pytest.raises(ValueError, match="distinct room"):
        session.validate()

