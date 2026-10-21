"""Offline checks for unified per-subtask and reused-recording preparation."""

import json
import shutil
import sys
from copy import deepcopy
from contextlib import nullcontext
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import h5py
import pytest

from memory_bench.processing.normalize_recording import NormalizationContext, normalize_recording
from memory_bench.processing.process_recordings import (
    prepare_run,
    process_recordings,
    select_run,
    validate_segment_index,
)
from memory_bench.session import Session
from memory_bench.tests.test_pipeline import canonical, freeze_intervents, material as material, snapshot
from memory_bench.utils.recordings import FrameReader, recording_metadata
from memory_bench.utils.scene_state import file_hash, read_json, write_json


@pytest.fixture
def collected(material, monkeypatch):
    session, _, _, manifests = material
    assignments_path = session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json")
    assignment = read_json(assignments_path)["instances"][0]
    tro = read_json(session.paths.output_root / "instance.json")
    template_path = session.paths.resolve(tro["scene_template"])
    template = read_json(template_path)
    template_path.write_text(json.dumps(template))
    tro["scene_template_sha256"] = file_hash(template_path)
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
        directory = raw_root / f"run_{number:04d}"
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
            source = directory / f"{key}.hdf5"
            canonical(source, entry, exit_state, session.segments[key]["activity"], terminal=False)
            with h5py.File(source, "a") as stream:
                stream["data"].attrs.update(
                    session_id=session.session_id, instance_id=1, case_id="A", segment_id=key,
                    run_id=directory.name, collection_complete=True,
                )
            write_json(directory / f"{key}_entry.json", entry)
            write_json(directory / f"{key}_exit.json", exit_state)
            segments[key] = {
                "hdf5": session.paths.relative(source), "demo": 0, "frames": [0, 2], "sha256": file_hash(source),
                "states": {"directory": session.paths.relative(directory),
                           "entry": f"{key}_entry.json", "exit": f"{key}_exit.json"},
                "validation": {"entry": True, "exit": True, "empty_hands": True, "stable": True,
                               "collision_free": True, "object_events": []},
            }
        record = {
            "format": "memory_bench.collection_run.v1", "run_id": directory.name,
            "session_id": session.session_id, "instance_id": 1,
            "case_id": "A", "instance": session.paths.relative(tro_path), "instance_sha256": file_hash(tro_path),
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
             "used": manifests[key]["objects"]["used"]}
        ]}
    inventory_path = session.paths.resolve(f"reports/{session.session_id}/source_inventory.json")
    write_json(inventory_path, inventory)

    def audit(source, normalized, demo):
        entry = recording_metadata(source, demo)["scene"]
        exit_state = deepcopy(entry)
        reader = FrameReader(normalized)
        try:
            exit_state["state"] = reader.state(len(reader.group["action"]))
        finally:
            reader.close()
        return [entry, exit_state], {"entry": True, "exit": True, "empty_hands": True, "stable": True}

    monkeypatch.setattr("memory_bench.processing.prepare_intervent.audit_boundaries", audit)
    tro["bindings"] = assignment["bindings"]
    tro["intervents"] = read_json(assignments_path.parent / "intervent_segments.json")["instances"]["1"]
    return session, assignment, tro, run, inventory_path


def test_selects_latest_numeric_complete_run_and_checks_tro(collected):
    session, assignment, _, run, _ = collected
    run(2)
    latest, record = run(10)
    run(11, "aborted")
    run(12, "in_progress")
    selected, _, _ = select_run(session, assignment)
    assert selected == latest
    assert select_run(session, assignment, 2)[0].parent.name == "run_0002"
    assert selected.name == "manifest.json"
    assert record["run_id"] == "run_0010"
    with pytest.raises(ValueError, match="No complete collection run"):
        select_run(session, assignment, 12)
    record["instance_sha256"] = "changed"
    latest.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="TRO hash"):
        select_run(session, assignment)


@pytest.mark.parametrize("corruption", ["run_id", "segment_order"])
def test_rejects_run_manifest_identity_or_order_mismatch(collected, corruption):
    session, assignment, _, run, _ = collected
    path, record = run()
    if corruption == "run_id":
        record["run_id"] = "run_0002"
    else:
        record["segment_ids"].reverse()
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="run_id|Keys then Query"):
        select_run(session, assignment)


def test_processing_reuses_fixed_intervents_and_resume_checks_hashes(collected, monkeypatch):
    session, _, _, run, _ = collected
    monkeypatch.setattr("memory_bench.processing.prepare_intervent.prepare_intervents", Mock(side_effect=AssertionError))
    run_path, _ = run()
    index_path = process_recordings(session)
    index = validate_segment_index(session, read_json(index_path))
    assert index["format"] == "memory_bench.segment_index.v3"
    assert index["complete"] is True
    assert set(index["instances"][0]["segments"]) == {"key_1", "query"}
    assert set(index["intervents"]["1"]) == {"intervent_1", "intervent_2"}
    instance = index["instances"][0]
    assert instance["run_manifest"] == session.paths.relative(run_path)
    assert instance["run_manifest_sha256"] == file_hash(run_path)
    source_identity = session.paths.resolve(next(iter(instance["segments"].values()))).parent / "source.json"
    assert read_json(source_identity) == {
        "run_manifest": session.paths.relative(run_path), "sha256": file_hash(run_path),
    }
    for path in [*index["instances"][0]["segments"].values(), *index["intervents"]["1"].values()]:
        manifest = read_json(session.paths.resolve(path))
        reader = FrameReader(session.paths.resolve(manifest["source"]["normalized_hdf5"]))
        assert len(reader.group["state_json"]) == len(reader.group["action"]) + 1 == 3
        reader.close()
        assert manifest["source"]["frames"] == [0, 2]
    frozen = file_hash(index_path)
    assert process_recordings(session, resume=True) == index_path
    assert file_hash(index_path) == frozen
    with pytest.raises(FileExistsError, match="--resume"):
        process_recordings(session)
    source = read_json(session.paths.resolve(index["instances"][0]["segments"]["key_1"]))["source"]["hdf5"]
    with h5py.File(session.paths.resolve(source), "a") as stream:
        stream["data/demo_0/action"][0, 0] = 0.75
    with pytest.raises(ValueError, match="changed"):
        process_recordings(session, resume=True)


def test_processed_run_pairs_and_composes_with_its_fixed_intervents(collected, material):
    from memory_bench.episodes.compose_episodes import compose_episode
    from memory_bench.episodes.pair_segments import match_sources
    from memory_bench.utils.segment_manifest import load_segments

    session, assignment, _, run, _ = collected
    _, _, bridge, _ = material
    run()
    index = read_json(process_recordings(session))
    groups = match_sources(session, [assignment], [index["instances"][0]["segments"]], index["intervents"], [0])
    assert all(groups[0]["segments"][key] == path for key, path in index["intervents"]["1"].items())
    recipe = read_json(compose_episode(session, groups[0], bridge))
    assert recipe["instance_id"] == 1
    assert recipe["case_id"] == assignment["case_id"]
    assert recipe["total_frames"] == 14
    assert set(load_segments(session, {**recipe["segments"], **recipe["bridges"]})) == {
        *session.segments, "bridge_00", "bridge_01", "bridge_02",
    }


def test_index_checks_each_kq_artifact_once(collected, monkeypatch):
    session, _, _, run, _ = collected
    run()
    index = read_json(process_recordings(session))
    hashes = Mock(wraps=file_hash)
    monkeypatch.setattr("memory_bench.processing.process_recordings.file_hash", hashes)
    monkeypatch.setattr("memory_bench.utils.segment_manifest.file_hash", hashes)
    validate_segment_index(session, index)
    paths = [call.args[0] for call in hashes.call_args_list]
    for path in index["instances"][0]["segments"].values():
        manifest = read_json(session.paths.resolve(path))
        for name in ("hdf5", "normalized_hdf5"):
            source = manifest["source"][name]
            assert source not in index["files"]
            assert paths.count(session.paths.resolve(source)) == 1
        for name in ("entry", "exit"):
            boundary = session.paths.resolve(manifest["states"]["directory"]) / manifest["states"][name]
            assert paths.count(boundary) == 1


@pytest.mark.parametrize("artifact", ["manifest", "normalized_hdf5", "entry", "exit"])
def test_index_rejects_changed_prepared_artifacts(collected, artifact):
    session, _, _, run, _ = collected
    run()
    index = read_json(process_recordings(session))
    path = session.paths.resolve(index["instances"][0]["segments"]["key_1"])
    manifest = read_json(path)
    if artifact == "normalized_hdf5":
        path = session.paths.resolve(manifest["source"][artifact])
    elif artifact in ("entry", "exit"):
        path = session.paths.resolve(manifest["states"]["directory"]) / manifest["states"][artifact]
    with path.open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="changed"):
        validate_segment_index(session, index)


@pytest.mark.parametrize("corruption", ["source_hash", "range", "demo", "run_id", "transfer", "world", "query_event"])
def test_rejects_invalid_run_sources_and_role_checks(collected, corruption):
    session, assignment, tro, run, _ = collected
    path, record = run()
    if corruption == "source_hash":
        record["segments"]["key_1"]["sha256"] = "wrong"
    elif corruption == "range":
        record["segments"]["key_1"]["frames"] = [1, 2]
    elif corruption == "demo":
        record["segments"]["key_1"]["demo"] = 1
    elif corruption == "run_id":
        item = record["segments"]["key_1"]
        raw = session.paths.resolve(item["hdf5"])
        with h5py.File(raw, "a") as stream:
            stream["data"].attrs["run_id"] = "run_0002"
        item["sha256"] = file_hash(raw)
    elif corruption == "transfer":
        record["transfers"] = []
    elif corruption == "world":
        entry = path.parent / "query_entry.json"
        value = read_json(entry)
        value["state"]["registry"]["object_registry"]["chocolate"]["root_link"]["pos"][0] = 9
        entry.write_text(json.dumps(value))
    else:
        record["segments"]["query"]["validation"]["object_events"] = [{"frame": 1, "forbidden": True}]
    with pytest.raises(ValueError):
        prepare_run(session, assignment, path, record, tro)


def test_processing_uses_recorded_reset_joints_with_pose_only_tro(collected):
    session, _, tro, run, _ = collected
    assert set(tro["robot"]) == {"position", "orientation"}
    tro_path = session.paths.resolve(f"sessions/{session.session_id}/tro/instance_0001.json")
    tro_path.write_text(json.dumps(tro))
    frozen = tro_path.read_bytes()
    run(joint_positions=[0.35, -0.4])

    index = read_json(process_recordings(session))

    assert index["complete"]
    assert tro_path.read_bytes() == frozen
    for path in index["instances"][0]["segments"].values():
        manifest = read_json(session.paths.resolve(path))
        entry = read_json(session.paths.resolve(manifest["states"]["directory"]) / manifest["states"]["entry"])
        assert entry["robot"]["joint_positions"] == [0.35, -0.4]


@pytest.mark.parametrize("corruption", ["position", "orientation", "joints"])
def test_processing_rejects_changed_sampled_pose_or_inconsistent_run_joints(collected, corruption):
    session, assignment, tro, run, _ = collected
    path, record = run(joint_positions=[0.35, -0.4])
    key = "query" if corruption == "joints" else "key_1"
    entry_path = path.parent / f"{key}_entry.json"
    entry = read_json(entry_path)
    robot_state = entry["state"]["registry"]["object_registry"]["robot"]
    if corruption == "position":
        entry["robot"]["position"][0] += 1.0
        robot_state["root_link"]["pos"][0] += 1.0
    elif corruption == "orientation":
        entry["robot"]["orientation"] = [0, 0, 1, 0]
        robot_state["root_link"]["ori"] = [0, 0, 1, 0]
    else:
        entry["robot"]["joint_positions"][0] += 0.5
        robot_state["joint_pos"][0] += 0.5
    entry_path.write_text(json.dumps(entry))
    source = session.paths.resolve(record["segments"][key]["hdf5"])
    with h5py.File(source, "a") as stream:
        stream["data"].attrs["scene_file"] = json.dumps(entry)
    record["segments"][key]["sha256"] = file_hash(source)

    with pytest.raises(ValueError, match="Collection entry pose"):
        prepare_run(session, assignment, path, record, tro)


def test_partial_index_blocks_export_and_can_resume_other_instances(collected, monkeypatch):
    session, assignment, tro, run, _ = collected
    run()
    assignments_path = session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json")
    assignments = read_json(assignments_path)
    assignments["instances"].append({**assignment, "instance_id": 2})
    assignments_path.write_text(json.dumps(assignments))
    allocation_path = assignments_path.parent / "intervent_segments.json"
    allocations = read_json(allocation_path)["instances"]
    allocations["2"] = {}
    for key, reference in allocations["1"].items():
        manifest = read_json(session.paths.resolve(reference))
        source = session.paths.resolve(manifest["source"]["hdf5"])
        second = source.with_name(source.stem + "_second.hdf5")
        second.write_bytes(source.read_bytes())
        manifest["source"].update(hdf5=second.name, normalized_hdf5=second.name)
        path = source.with_name(source.stem + "_second.json")
        write_json(path, manifest)
        allocations["2"][key] = session.paths.relative(path)
    freeze_intervents(session, allocations)
    tro_path = session.paths.resolve(f"sessions/{session.session_id}/tro/instance_0001.json")
    template_path = session.paths.resolve(tro["scene_template"])
    template = read_json(template_path)
    template["metadata"]["task"]["memory_bench_inputs"]["assignments_sha256"] = file_hash(assignments_path)
    template["metadata"]["task"]["memory_bench_inputs"]["intervents_sha256"] = file_hash(allocation_path)
    template_path.write_text(json.dumps(template))
    tro["scene_template_sha256"] = file_hash(template_path)
    tro_path.write_text(json.dumps(tro))
    run_path = session.paths.resolve(f"recordings/{session.session_id}/instance_0001/run_0001/manifest.json")
    record = read_json(run_path)
    record["instance_sha256"] = file_hash(tro_path)
    run_path.write_text(json.dumps(record))
    index = read_json(process_recordings(session, instance_id=1))
    assert index["complete"] is False
    assert len(index["instances"]) == 1
    with pytest.raises(ValueError, match="partial"):
        validate_segment_index(session, index)
    validate_segment_index(session, index, require_complete=False)
    tro2 = {**tro, "instance_id": 2}
    tro2_path = tro_path.with_name("instance_0002.json")
    write_json(tro2_path, tro2)
    raw2 = run_path.parent.parent.with_name("instance_0002") / run_path.parent.name
    shutil.copytree(run_path.parent, raw2)
    record2 = deepcopy(record)
    record2.update(instance_id=2, instance=session.paths.relative(tro2_path), instance_sha256=file_hash(tro2_path))
    for item in record2["segments"].values():
        item["hdf5"] = session.paths.relative(raw2 / session.paths.resolve(item["hdf5"]).name)
        item["states"]["directory"] = session.paths.relative(raw2)
        with h5py.File(session.paths.resolve(item["hdf5"]), "a") as stream:
            stream["data"].attrs["instance_id"] = 2
        item["sha256"] = file_hash(session.paths.resolve(item["hdf5"]))
    (raw2 / "manifest.json").write_text(json.dumps(record2))
    hashes = Mock(wraps=file_hash)
    monkeypatch.setattr("memory_bench.processing.process_recordings.file_hash", hashes)
    monkeypatch.setattr("memory_bench.utils.segment_manifest.file_hash", hashes)
    completed = read_json(process_recordings(session, instance_id=2, resume=True))
    old_source = session.paths.resolve(record["segments"]["key_1"]["hdf5"])
    assert [call.args[0] for call in hashes.call_args_list].count(old_source) == 1
    assert completed["complete"] is True
    validate_segment_index(session, completed)


def test_rejects_previous_segment_index_format(collected):
    session, _, _, _, _ = collected

    with pytest.raises(ValueError, match="format/session"):
        validate_segment_index(session, {"format": "memory_bench.segment_index.v1", "session_id": session.session_id})


def test_intervent_duplicate_candidates_do_not_underfill_silently(collected):
    session, _, _, run, inventory_path = collected
    run()
    assignments_path = session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json")
    assignments = read_json(assignments_path)
    assignments["instances"].append({"instance_id": 2, "case_id": "A"})
    assignments_path.write_text(json.dumps(assignments))
    (assignments_path.parent / "intervent_segments.json").unlink()
    # Exercise I selection directly: duplicate inventory rows cannot provide two sources.
    from memory_bench.processing.prepare_intervent import prepare_intervents

    inventory = read_json(inventory_path)
    for task in inventory["tasks"].values():
        task["candidates"] *= 2
    with pytest.raises(ValueError, match="Only 1/2"):
        prepare_intervents(session, inventory)


def test_intervents_are_prepared_once_before_collection_and_reused(collected, monkeypatch):
    from memory_bench.processing import prepare_intervent as preparation

    session, _, _, _, inventory_path = collected
    root = session.paths.resolve(f"sessions/{session.session_id}")
    (root / "intervent_segments.json").unlink()
    cases = (root / "case_assignments.json").read_bytes()
    inventory = read_json(inventory_path)
    original_sources = {
        item["hdf5"]: file_hash(item["hdf5"])
        for task in inventory["tasks"].values() for item in task["candidates"]
    }
    result = preparation.prepare_intervents(session, inventory)
    frozen = (root / "intervent_segments.json").read_bytes()
    assert set(result["1"]) == {"intervent_1", "intervent_2"}
    monkeypatch.setattr(preparation, "prepare_intervent", Mock(side_effect=AssertionError("Do not prepare twice")))
    assert "inventory_sha256" not in read_json(root / "intervent_segments.json")
    # Candidate changes must not invalidate or reassign material already selected.
    for task in inventory["tasks"].values():
        task["candidates"] = []
    assert preparation.prepare_intervents(session, inventory, resume=True) == result
    assert (root / "intervent_segments.json").read_bytes() == frozen
    assert (root / "case_assignments.json").read_bytes() == cases
    assert all(file_hash(path) == expected for path, expected in original_sources.items())


def test_normalization_ignores_archived_rollbacks_and_keeps_terminal(tmp_path):
    entry = snapshot({"robot": 0, "toy": 0})
    exit_state = snapshot({"robot": 0, "toy": 1})
    raw, normalized = tmp_path / "raw.hdf5", tmp_path / "normalized.hdf5"
    canonical(raw, entry, exit_state, "test", terminal=False)
    terminal = tmp_path / "exit.json"
    write_json(terminal, exit_state)
    with h5py.File(raw, "a") as stream:
        stream["data/demo_0"].create_group("rollbacks").create_dataset("failed_actions", data=[99, 99, 99])
    normalize_recording(None, raw, normalized, terminal=terminal)
    reader = FrameReader(normalized)
    try:
        assert len(reader.group["action"]) == 2
        assert "rollbacks" not in reader.group
        assert reader.state(2) == exit_state["state"]
    finally:
        reader.close()


def test_normalization_reuses_one_schema_environment_for_subtasks(tmp_path, monkeypatch):
    scene = snapshot({"robot": 0, "toy": 0})
    fake_og = ModuleType("omnigibson")
    fake_og.Environment = Mock(return_value=SimpleNamespace(scene=object()))
    fake_og.clear = Mock()
    fake_og.sim = SimpleNamespace(
        viewer_width=640, viewer_height=480,
        deserialize=lambda value: ([scene["state"]], len(value)),
    )
    wrapper = ModuleType("omnigibson.envs.data_wrapper")
    wrapper._add_recorded_non_kin_states_to_scene_file = lambda *_: None
    wrapper._align_scene_object_states_with_recorded_schema = lambda *_: None
    macros = ModuleType("omnigibson.macros")
    macros.gm = SimpleNamespace(unlocked=lambda: nullcontext())
    monkeypatch.setitem(sys.modules, "omnigibson", fake_og)
    monkeypatch.setitem(sys.modules, "omnigibson.envs.data_wrapper", wrapper)
    monkeypatch.setitem(sys.modules, "omnigibson.macros", macros)
    with NormalizationContext(None) as context:
        for key in ("key_1", "query"):
            source = tmp_path / f"{key}.hdf5"
            canonical(source, scene, scene, key)
            with h5py.File(source, "a") as stream:
                group = stream["data/demo_0"]
                del group["state_json"]
                group.create_dataset("state", data=[[0.0], [0.0], [0.0]])
                group.create_dataset("state_size", data=[1, 1, 1])
            normalize_recording(None, source, tmp_path / f"{key}_normalized.hdf5", context=context)
    assert fake_og.Environment.call_count == 1
    assert fake_og.clear.call_count == 1
