"""Variant assignment uses available TROs and carries their count through later stages."""

from collections import Counter
import json
from pathlib import Path

import pytest
import yaml

from memory_bench.collection.collect_session import collect_session
from memory_bench.collection.prepare_task import assign_variants, init
from memory_bench.session import Session


@pytest.fixture
def sampling_session(tmp_path, monkeypatch):
    config = yaml.safe_load(Path("memory_bench/configs/mb_retrieve_mug_from_top_cabinet.yaml").read_text())
    config["paths"] = {
        "data_root": str(tmp_path / "data"),
        "intervent_rawdata_root": str(tmp_path / "raw"),
        "output_root": str(tmp_path / "output"),
    }
    monkeypatch.setenv("OMNIGIBSON_DATA_PATH", config["paths"]["data_root"])
    monkeypatch.setenv("MB_ROOT", config["paths"]["output_root"])
    monkeypatch.setenv("OMNIGIBSON_TASK_INSTANCES_DATASET", "memory-bench-task-instances")
    config_path = tmp_path / "session.yaml"
    config_path.write_text(yaml.safe_dump(config))
    session = Session.load(config_path)
    json_dir = session.paths.data_root / "memory-bench-task-instances/scenes/house_single_floor/json"
    json_dir.mkdir(parents=True)
    bindings = {
        "mug.n.04_1": "mug_ntgftr_0",
        "countertop.n.01_1": "bar_udatjt_0",
        **{f"cabinet.n.01_{index + 1}": f"top_cabinet_lkxmne_{index}" for index in range(3)},
    }
    for activity in session.task.key_activities + (session.task.query_activity,):
        path = json_dir / f"house_single_floor_task_{activity}_0_0_template.json"
        path.write_text(json.dumps({"metadata": {"task": {"inst_to_name": bindings}}}))
    prefix = f"house_single_floor_task_{session.task.key_activities[0]}"
    instance_dir = json_dir / f"{prefix}_instances"
    instance_dir.mkdir()
    return session, config_path, instance_dir, prefix, bindings


@pytest.mark.parametrize("source_ids", [[1, 4, 9], list(range(1, 11)), list(range(1, 152))])
def test_assigns_all_available_tros_and_reloads_actual_count(sampling_session, source_ids):
    session, config_path, instance_dir, prefix, bindings = sampling_session
    originals = {}
    for source_id in source_ids:
        path = instance_dir / f"{prefix}_0_{source_id}_template-tro_state.json"
        original = json.dumps({instance: {"object_name": name} for instance, name in bindings.items()})
        path.write_text(original)
        originals[path] = original
    (instance_dir / "unrelated-tro_state.json").write_text("{}")
    assert session.config["instance_sampling"]["count"] == 150

    assign_variants(session)

    assignment_path = session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json")
    saved = assignment_path.read_text()
    manifest = json.loads(saved)
    assert manifest["count"] == len(source_ids)
    assert [entry["source_task_instance_id"] for entry in manifest["instances"]] == sorted(source_ids)
    assert [entry["instance_id"] for entry in manifest["instances"]] == list(range(1, len(source_ids) + 1))
    counts = Counter(entry["case_id"] for entry in manifest["instances"])
    assert max(counts.values()) - min(counts.values()) <= 1
    for entry in manifest["instances"]:
        tro = json.loads(Path(entry["tro"]).read_text())
        assert tro["inst_to_name"] == entry["bindings"]["key_1"]
        for instance, name in tro["inst_to_name"].items():
            assert tro[instance]["object_name"] == name
    assert all(path.read_text() == original for path, original in originals.items())
    assert session.config["instance_sampling"]["count"] == 150
    assert session.instance_count == len(source_ids)
    reloaded = Session.load(config_path)
    assert reloaded.config["instance_sampling"]["count"] == 150
    assert reloaded.instance_count == len(source_ids)
    with pytest.raises(ValueError, match=f"instance_id must be in 1..{len(source_ids)}"):
        collect_session(reloaded, len(source_ids) + 1)
    assert yaml.safe_load(config_path.read_text())["instance_sampling"]["count"] == 150
    with pytest.raises(ValueError, match="already frozen"):
        assign_variants(session)
    assert assignment_path.read_text() == saved


def test_assignment_rejects_an_empty_tro_directory(sampling_session):
    session, _, instance_dir, _, _ = sampling_session
    (instance_dir / "unrelated-tro_state.json").write_text("{}")
    with pytest.raises(ValueError, match="No sampled .* TRO files found"):
        assign_variants(session)
    assert not session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json").exists()


def test_initialization_remains_idempotent_after_assignment(sampling_session):
    session, config_path, instance_dir, prefix, bindings = sampling_session
    stable_path = (
        session.paths.data_root
        / "2026-challenge-task-instances/scenes/house_single_floor/json/house_single_floor_stable.json"
    )
    stable_path.parent.mkdir(parents=True)
    stable_path.write_text("{}")
    init(session)
    snapshot_path = session.paths.resolve(f"sessions/{session.session_id}/session.json")
    saved = snapshot_path.read_text()
    tro_path = instance_dir / f"{prefix}_0_1_template-tro_state.json"
    tro_path.write_text(json.dumps({instance: {"object_name": name} for instance, name in bindings.items()}))
    assign_variants(session)

    reloaded = Session.load(config_path)
    assert reloaded.instance_count == 1
    init(reloaded)

    assert snapshot_path.read_text() == saved
