"""Four-step regressions: raw sources, one demo, independent bridges and Query restoration."""

import json
import sys
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import h5py
import pytest

from memory_bench.collection.prepare_intervent import prepare_intervents
from memory_bench.episodes.build_bridges import build_episode, select_demo
from memory_bench.episodes.compose_episodes import compose_episode
from memory_bench.session import Session
from memory_bench.tests.test_pipeline import canonical, material as material, snapshot
from memory_bench.utils.recordings import FrameReader, RecordingContext
from memory_bench.utils.scene_state import read_json, scene_objects, write_json


@pytest.fixture
def collected(material, monkeypatch):
    session, _, _, manifests = material
    assignments_path = session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json")
    assignment = read_json(assignments_path)["instances"][0]
    tro = read_json(session.paths.output_root / "instance.json")
    tro_path = assignments_path.parent / "tro" / "instance_0001.json"
    write_json(tro_path, tro)
    usage = {
        definition["activity"]: {
            "used": ["chocolate_bar.n.01_1"], "read": ["chocolate_bar.n.01_1"],
            "write": ["chocolate_bar.n.01_1"], "protected": ["chocolate_bar.n.01_1"],
        }
        for key, definition in session.segments.items() if key in [*session.key_ids, session.query_id]
    }
    monkeypatch.setattr(Session, "task", property(lambda _: SimpleNamespace(
        object_usage=usage, instruction_for=lambda activity, case_id: activity, key_information={"key_1": "location"},
    )))
    raw_root = session.paths.resolve(f"recordings/{session.session_id}/instance_0001")

    def run(number=1, status="complete", joint_positions=None):
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
                if joint_positions is not None:
                    boundary["robot"]["joint_positions"] = list(joint_positions)
                    boundary["robot"]["controlled_joint_indices"] = list(range(len(joint_positions)))
                    boundary["state"]["registry"]["object_registry"]["robot"]["joint_pos"] = list(joint_positions)
            filename = "key_01" if key == "key_1" else key
            source = directory / f"{filename}.hdf5"
            canonical(source, entry, exit_state, session.segments[key]["activity"], terminal=False)
            with h5py.File(source, "a") as stream:
                stream["data"].attrs.update(
                    session_id=session.session_id, instance_id=1, case_id="A", segment_id=key,
                    run_id=directory.name, collection_complete=True,
                )
            write_json(directory / f"{filename}_entry.json", entry)
            write_json(directory / f"{filename}_exit.json", exit_state)
            segments[key] = {
                "hdf5": session.paths.relative(source), "demo": 0, "frames": [0, 2],
                "states": {"directory": session.paths.relative(directory),
                           "entry": f"{filename}_entry.json", "exit": f"{filename}_exit.json"},
                "validation": {"entry": True, "exit": True, "empty_hands": True, "stable": True,
                               "collision_free": True, "object_events": []},
            }
        record = {
            "format": "memory_bench.collection_run.v1", "run_id": directory.name,
            "session_id": session.session_id, "instance_id": 1,
            "case_id": "A", "instance": session.paths.relative(tro_path),
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
    inventory_path = session.paths.resolve(f"reports/{session.session_id}/source_inventory.json")
    write_json(inventory_path, inventory)

    tro["bindings"] = assignment["bindings"]
    tro["intervents"] = read_json(assignments_path.parent / "intervent_assignments.json")["instances"]["1"]
    return session, {**assignment, "robot": tro["robot"]}, tro, run, inventory_path


def test_selects_only_requested_demo(collected):
    session, instance, _, run, _ = collected
    first, _ = run(1)
    second, _ = run(2)
    assert select_demo(session, instance)[0] == first
    assert select_demo(session, instance, 2)[0] == second


def test_missing_first_demo_does_not_fall_back(collected):
    session, instance, _, run, _ = collected
    run(2)
    with pytest.raises(FileNotFoundError):
        select_demo(session, instance)


def test_incomplete_first_demo_does_not_fall_back(collected):
    session, instance, _, run, _ = collected
    run(1, "in_progress")
    run(2)
    with pytest.raises(ValueError, match="incomplete"):
        select_demo(session, instance)


def test_bridges_and_composition_are_independent_and_query_is_restorable(collected, material, monkeypatch):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    run(1)
    run(2)
    raw = {path: path.read_bytes() for path in session.paths.output_root.rglob("*.hdf5")}
    path = build_episode(session, 1, session.composition["task_orders"][0], bridge_builder=bridge)
    recipe = read_json(path)
    assert recipe["collection_demo"] == "demo_001"
    assert len(recipe["bridges"]) == 3
    assert not session.paths.resolve(f"episodes/{session.session_id}").exists()
    assert not session.paths.resolve("segments").exists()
    assert not list(session.paths.output_root.rglob("*normalized*"))
    assert all(source.read_bytes() == data for source, data in raw.items())
    monkeypatch.setattr("memory_bench.episodes.synthesize_bridges.synthesize_bridge", Mock(side_effect=AssertionError))
    compose_episode(session, recipe)
    result = read_json(path)
    assert result["total_frames"] == 14
    restored = read_json(session.paths.resolve(result["query_start"]))
    with h5py.File(session.paths.resolve(result["hdf5"]), "r") as stream:
        assert restored["state"] == json.loads(stream["data/demo_0/state_json"][result["query_start_frame"]])
        assert len(stream["data/demo_0/state_json"]) == len(stream["data/demo_0/action"]) + 1
    objects = scene_objects(restored)
    assert objects["chocolate"]["root_link"]["pos"][0] == 3
    assert objects["toy"]["root_link"]["pos"][0] == 1
    assert objects["hammer"]["root_link"]["pos"][0] == 2
    assert "robot" in restored["objects_info"]["init_info"]
    assert restored["robot"]["name"] == "robot"
    assert restored["metadata"]["task"]["inst_to_name"] == read_json(
        session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json")
    )["instances"][0]["bindings"]["query"]
    assert not list(session.paths.output_root.rglob("segment_index.json"))
    assert not list(session.paths.output_root.rglob("material_groups.json"))


@pytest.mark.parametrize("field", ["run_id", "source_path", "transfer", "instance_path", "query_event"])
def test_rejects_changed_or_mixed_collection_demo(collected, material, field):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    path, record = run()
    if field == "run_id":
        record["run_id"] = "demo_002"
    elif field == "source_path":
        record["segments"]["key_1"]["hdf5"] = "another_demo/key_01.hdf5"
    elif field == "transfer":
        record["transfers"] = []
    elif field == "instance_path":
        record["instance"] = "tro/instance_0002.json"
    else:
        record["segments"]["query"]["validation"]["object_events"] = [{"forbidden": True}]
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        build_episode(session, 1, session.composition["task_orders"][0], bridge_builder=bridge)


def test_raw_source_metadata_change_does_not_require_content_digest(collected, material):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0], bridge_builder=bridge))
    source = session.paths.resolve(recipe["segments"]["intervent_1"]["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        stream["data"].attrs["note"] = "metadata update"
    assert compose_episode(session, recipe).is_file()


def test_composition_rejects_robot_teleport_between_segments(collected, material):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0], bridge_builder=bridge))
    source = session.paths.resolve(recipe["segments"]["intervent_2"]["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        state = json.loads(stream["data/demo_0/state_json"][0])
        scene_objects(state)["robot"]["root_link"]["pos"][0] += 1
        stream["data/demo_0/state_json"][0] = json.dumps(state)
    with pytest.raises(ValueError, match="intervent_2 handoff"):
        compose_episode(session, recipe)
    assert not session.paths.resolve(f"episodes/{session.session_id}/episode_00000000.hdf5").exists()


def test_composition_rejects_bridge_planned_in_different_inherited_world(collected, material):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0], bridge_builder=bridge))
    source = session.paths.resolve(recipe["segments"]["intervent_1"]["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        state = json.loads(stream["data/demo_0/state_json"][-1])
        scene_objects(state)["toy"]["root_link"]["pos"][0] += 1
        stream["data/demo_0/state_json"][-1] = json.dumps(state)
    with pytest.raises(ValueError, match="bridge_00 preconditions"):
        compose_episode(session, recipe)
    assert not session.paths.resolve(f"episodes/{session.session_id}/episode_00000000.hdf5").exists()


@pytest.mark.parametrize("field,value", [("session_id", "other"), ("instance_id", 2), ("episode_id", "episode_00000001")])
def test_composition_rejects_mismatched_recipe_identity(collected, material, field, value):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0], bridge_builder=bridge))
    recipe[field] = value
    with pytest.raises(ValueError, match="Recipe identity"):
        compose_episode(session, recipe)


def test_composition_requires_selected_demo_to_remain_complete(collected, material):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    path, record = run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0], bridge_builder=bridge))
    record["status"] = "in_progress"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="collection demo is incomplete"):
        compose_episode(session, recipe)


def test_transient_change_is_rejected_without_publishing_episode(collected, material):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    _, record = run()
    # Keep boundaries intact but move an unrelated object in the middle of the Key.
    source = session.paths.resolve(record["segments"]["key_1"]["hdf5"])
    with h5py.File(source, "a") as stream:
        state = json.loads(stream["data/demo_0/state_json"][1])
        scene_objects(state)["toy"]["root_link"]["pos"][0] = 99
        stream["data/demo_0/state_json"][1] = json.dumps(state)
    manifest = source.parent / "manifest.json"
    manifest.write_text(json.dumps(record))
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0], bridge_builder=bridge))
    with pytest.raises(ValueError, match="Undeclared state changes"):
        compose_episode(session, recipe)
    root = session.paths.resolve(f"episodes/{session.session_id}")
    assert not (root / "episode_00000000.hdf5").exists()
    assert not (root / "episode_00000000_query_start.json").exists()


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


def test_world_audit_uses_actions_after_closing_source_readers(material, monkeypatch):
    from memory_bench.tools.audit_world import audit_world

    session, _, _, segments = material
    session.config.update(mode="intervent_debug", task_order=["intervent_1", "intervent_2"])
    references = {key: segments[key] for key in session.task_order}
    source = session.paths.resolve(references["intervent_2"]["source"]["hdf5"])
    with h5py.File(source, "a") as stream:
        stream["data/demo_0/action"][:] = 9
    world_path = session.paths.resolve("scene.json")
    world = read_json(world_path)
    scene = SimpleNamespace(
        robots=[SimpleNamespace(name="robot")], objects=[], restore=Mock(), load_state=Mock(), save=lambda **_: world,
    )

    def step(action):
        assert action.tolist() == [0.25] * 23
        raise RuntimeError("First source action reached the simulator")

    modules = {
        "omnigibson": SimpleNamespace(
            Environment=lambda **_: SimpleNamespace(scene=scene, step=step),
            sim=SimpleNamespace(_refresh_state_caches=Mock()),
        ),
        "omnigibson.macros": SimpleNamespace(gm=SimpleNamespace(unlocked=lambda: nullcontext())),
        "omnigibson.utils.config_utils": SimpleNamespace(TorchEncoder=json.JSONEncoder),
        "omnigibson.utils.motion_planning_utils": SimpleNamespace(detect_robot_collision_in_sim=Mock()),
        "omnigibson.utils.python_utils": SimpleNamespace(recursively_convert_to_torch=lambda state: state),
        "omnigibson.utils.registry_utils": SimpleNamespace(m=SimpleNamespace(STABILIZE_SKIPPED_OBJECTS=False)),
        "omnigibson.utils.usd_utils": SimpleNamespace(RigidContactAPI=Mock()),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr("memory_bench.tools.audit_world._build_config", lambda *_: {})
    with pytest.raises(RuntimeError, match="First source action reached"):
        audit_world(session, references, world_path)


def test_bridge_retry_reuses_completed_raw_recording(collected, material):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    run()
    order = session.composition["task_orders"][0]

    def interrupted(session, scene, previous, entry, output):
        if output.stem == "bridge_01":
            raise RuntimeError("interrupted")
        return bridge(session, scene, previous, entry, output)

    with pytest.raises(RuntimeError, match="interrupted"):
        build_episode(session, 1, order, bridge_builder=interrupted)
    first = session.paths.resolve(f"bridges/{session.session_id}/episode_00000000/bridge_00.hdf5")
    saved = first.read_bytes()
    resumed = Mock(side_effect=bridge)
    build_episode(session, 1, order, bridge_builder=resumed)
    assert resumed.call_count == 2
    assert first.read_bytes() == saved


def test_recipe_cannot_replace_query_with_another_demo(collected, material):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    run(1)
    _, other = run(2)
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0], bridge_builder=bridge))
    recipe["segments"]["query"]["source"].update(
        {name: other["segments"]["query"][name] for name in ("hdf5", "demo", "frames")}
    )
    with pytest.raises(ValueError, match="mixes sources"):
        compose_episode(session, recipe)


def test_terminal_snapshot_still_respects_task_write_scope(collected, material):
    session, _, _, run, _ = collected
    _, _, bridge, _ = material
    path, _ = run()
    recipe = read_json(build_episode(session, 1, session.composition["task_orders"][0], bridge_builder=bridge))
    exit_path = path.parent / "query_exit.json"
    exit_state = read_json(exit_path)
    scene_objects(exit_state)["toy"]["root_link"]["pos"][0] = 99
    exit_path.write_text(json.dumps(exit_state))
    with pytest.raises(ValueError, match="Undeclared state changes"):
        compose_episode(session, recipe)


def test_preparation_only_freezes_raw_sources_and_reuses_selection(collected, monkeypatch):
    session, _, _, _, inventory_path = collected
    root = session.paths.resolve(f"sessions/{session.session_id}")
    (root / "intervent_assignments.json").unlink()
    cases = (root / "case_assignments.json").read_bytes()
    inventory = read_json(inventory_path)
    sources = {item["hdf5"]: session.paths.resolve(item["hdf5"]).read_bytes()
               for task in inventory["tasks"].values() for item in task["candidates"]}

    def audit(source, demo):
        with FrameReader(source, demo) as reader:
            snapshots = [reader.snapshot(frame) for frame in (0, len(reader.group["action"]))]
        return snapshots, {"entry": True, "exit": True, "empty_hands": True, "stable": True}

    monkeypatch.setattr("memory_bench.collection.prepare_intervent.audit_boundaries", audit)
    assigned = prepare_intervents(session, inventory)
    frozen = (root / "intervent_assignments.json").read_bytes()
    for task in inventory["tasks"].values():
        task["candidates"] = []
    assert prepare_intervents(session, inventory) == assigned
    assert (root / "intervent_assignments.json").read_bytes() == frozen
    assert (root / "case_assignments.json").read_bytes() == cases
    assert all(session.paths.resolve(path).read_bytes() == content for path, content in sources.items())
    assert not session.paths.resolve("segments").exists()
    assert not list(session.paths.output_root.rglob("*normalized*"))


def test_old_allocation_migration_preserves_sources_and_world(collected, material):
    session, _, tro, _, _ = collected
    _, pairing, _, _ = material
    root = session.paths.resolve(f"sessions/{session.session_id}")
    (root / "intervent_assignments.json").unlink()
    references = {key: f"{key}.json" for key in pairing["segments"] if key.startswith("intervent")}
    previous = root / "intervent_segments.json"
    cases = (root / "case_assignments.json").read_bytes()
    write_json(previous, {
        "session_id": session.session_id,
        "instances": {"1": references},
    })
    template = read_json(session.paths.resolve(tro["scene_template"]))
    template["metadata"]["task"]["memory_bench_inputs"].update(
        intervents=session.paths.relative(previous),
    )
    template_path = root / "scene_template.json"
    write_json(template_path, template)
    tro_path = root / "tro/instance_0001.json"
    original = read_json(tro_path)
    original["scene_template"] = session.paths.relative(template_path)
    tro_path.write_text(json.dumps(original))
    selected = prepare_intervents(session, {})
    assert selected["1"]["intervent_1"]["hdf5"] == read_json(session.paths.resolve(references["intervent_1"]))["source"]["hdf5"]
    assert read_json(template_path)["state"] == template["state"]
    assert read_json(tro_path)["state"] == original["state"]
    assert (root / "case_assignments.json").read_bytes() == cases
    from memory_bench.utils.instance_scene import load_instance_scene
    assert load_instance_scene(session, tro_path)[0]["intervents"] == selected["1"]


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


def test_query_snapshot_binds_agent_to_actual_episode_robot(material):
    from memory_bench.episodes.compose_episodes import write_sequence
    session, _, _, segments = material
    query = segments["query"]
    entry = read_json(session.paths.resolve(query["states"]["entry"]))
    bindings = {"query": {"agent.n.01_1": "cached_sampling_robot", "chocolate_bar.n.01_1": "chocolate"}}
    result = write_sequence(session, entry, [query], set(), "episode_00000000", bindings)
    restored = read_json(session.paths.resolve(result["query_start"]))
    assert restored["metadata"]["task"]["inst_to_name"]["agent.n.01_1"] == restored["robot"]["name"]
    assert result["annotations"][0]["bindings"]["agent.n.01_1"] == restored["robot"]["name"]
