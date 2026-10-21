"""Variant assignment uses available TROs and carries their count through later stages."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

import h5py
import pytest
import yaml

from memory_bench.collection.collect_session import collect_session
from memory_bench.collection import prepare_session
from memory_bench.collection.prepare_session import (
    _remap_tro,
    _shared_task_bindings,
    assemble_scene_assets,
    assign_variants,
    init,
    validate_assignments,
)
from memory_bench.collection.sample_instances import _enrich_stable_scene
from memory_bench.collection.prepare_intervent import scan_sources
from memory_bench.session import Session


def template_snapshot(bindings, models=None):
    models = {} if models is None else models
    initializers = {}
    for name in bindings.values():
        category, model = models[name] if name in models else name.rsplit("_", 2)[:2]
        initializers[name] = {
            "class_module": "omnigibson.objects.dataset_object",
            "class_name": "DatasetObject",
            "args": {"name": name, "category": category, "model": model, "scale": [1, 1, 1]},
        }
    return {
        "metadata": {"task": {"inst_to_name": bindings}},
        "objects_info": {"init_info": initializers},
        "state": {
            "registry": {
                "object_registry": {name: {"object_name": name} for name in bindings.values()},
                "system_registry": {},
            }
        },
    }


@pytest.fixture
def sampling_session(tmp_path, monkeypatch):
    config = yaml.safe_load(Path("memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml").read_text())
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
    json_dir = session.paths.data_root / "memory-bench-task-instances/scenes/house_double_floor_lower/json"
    json_dir.mkdir(parents=True)
    bindings = {
        "chocolate_bar.n.01_1": "chocolate_bar_231",
        "breakfast_table.n.01_1": "breakfast_table_xftrki_0",
        **{f"cabinet.n.01_{index + 1}": f"top_cabinet_tynnnw_{index}" for index in range(2)},
    }
    for activity in session.task.key_activities + (session.task.query_activity,):
        path = json_dir / f"house_double_floor_lower_task_{activity}_0_0_template.json"
        path.write_text(json.dumps(template_snapshot(bindings, {"chocolate_bar_231": ("chocolate_bar", "dxnisi")})))
    stable = template_snapshot({instance: name for instance, name in bindings.items() if instance != "chocolate_bar.n.01_1"})
    (json_dir / "house_double_floor_lower_stable.json").write_text(json.dumps(stable))
    prefix = f"house_double_floor_lower_task_{session.task.key_activities[0]}"
    instance_dir = json_dir / f"{prefix}_instances"
    instance_dir.mkdir()
    return session, config_path, instance_dir, prefix, bindings


@pytest.mark.parametrize("source_ids", [[1, 4, 9], list(range(1, 11)), list(range(1, 152))])
def test_assigns_all_available_tros_and_reloads_actual_count(sampling_session, source_ids):
    session, config_path, instance_dir, prefix, bindings = sampling_session
    config_before = config_path.read_bytes()
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
    assert manifest["asset_requirements"]["chocolate_bar"] == ["chocolate_bar_231"]
    assert manifest["asset_sources"]["chocolate_bar_231"] == "key_1"
    assert manifest["count"] == len(source_ids)
    assert [entry["source_task_instance_id"] for entry in manifest["instances"]] == sorted(source_ids)
    assert [entry["instance_id"] for entry in manifest["instances"]] == list(range(1, len(source_ids) + 1))
    counts = Counter(entry["case_id"] for entry in manifest["instances"])
    assert max(counts.values()) - min(counts.values()) <= 1
    for entry in manifest["instances"]:
        tro = _remap_tro(json.loads(Path(entry["source_tro"]).read_text()), bindings, entry["bindings"]["key_1"])
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
    assert config_path.read_bytes() == config_before
    session_root = assignment_path.parent
    frozen = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in session_root.rglob("*.json")}
    assign_variants(session)
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == value for path, value in frozen.items())
    assert assignment_path.read_text() == saved
    assert not (session_root / "tro").exists()
    assert not (session_root / "instances.json").exists()
    with pytest.raises(ValueError, match="does not match the current inputs"):
        assign_variants(session, seed=999)
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == value for path, value in frozen.items())


def test_source_scan_reuses_inventory(sampling_session, monkeypatch):
    session, _, _, _, _ = sampling_session
    source = session.paths.intervent_rawdata_root / "task-0000/episode_0001.hdf5"
    source.parent.mkdir(parents=True)
    with h5py.File(source, "w") as stream:
        stream.create_dataset("data/demo_0/action", data=[[0.0]])
    source_before = source.read_bytes()
    monkeypatch.setattr(
        "memory_bench.collection.prepare_intervent.inspect_source",
        lambda path, *args: {"hdf5": str(path), "demo": 0},
    )
    report = scan_sources(session)
    assert report["tasks"]["intervent_1"]["compatible_metadata"] == 1
    path = session.paths.resolve(f"reports/{session.session_id}/source_inventory.json")
    saved = path.read_bytes(), path.stat().st_mtime_ns
    assert scan_sources(session) == report
    assert (path.read_bytes(), path.stat().st_mtime_ns) == saved
    assert source.read_bytes() == source_before


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
        / "2026-challenge-task-instances/scenes/house_double_floor_lower/json/house_double_floor_lower_stable.json"
    )
    stable_path.parent.mkdir(parents=True)
    stable_path.write_bytes((instance_dir.parent / "house_double_floor_lower_stable.json").read_bytes())
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


def test_query_uses_first_key_identity_without_rewriting_templates(sampling_session):
    session, config_path, instance_dir, prefix, bindings = sampling_session
    query_path = instance_dir.parent / f"house_double_floor_lower_task_{session.task.query_activity}_0_0_template.json"
    query_bindings = {**bindings, "chocolate_bar.n.01_1": "chocolate_bar_987"}
    query = template_snapshot(query_bindings, {"chocolate_bar_987": ("chocolate_bar", "dxnisi")})
    # An unbound copy of the same model must not make role resolution ambiguous.
    query["objects_info"]["init_info"]["chocolate_bar_decoy"] = deepcopy(query["objects_info"]["init_info"]["chocolate_bar_987"])
    query["objects_info"]["init_info"]["chocolate_bar_decoy"]["args"]["name"] = "chocolate_bar_decoy"
    query_path.write_text(json.dumps(query))
    originals = {path: path.read_bytes() for path in instance_dir.parent.glob("*.json")}
    config_before = config_path.read_bytes()
    (instance_dir / f"{prefix}_0_1_template-tro_state.json").write_text(
        json.dumps({instance: {"object_name": name} for instance, name in bindings.items()})
    )

    assign_variants(session)

    manifest = json.loads(session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json").read_text())
    instance = manifest["instances"][0]
    assert instance["bindings"]["key_1"]["chocolate_bar.n.01_1"] == "chocolate_bar_231"
    assert instance["bindings"]["query"]["chocolate_bar.n.01_1"] == "chocolate_bar_231"
    stable = json.loads((instance_dir.parent / "house_double_floor_lower_stable.json").read_text())
    enriched, _ = _enrich_stable_scene(session, instance, stable, manifest["asset_sources"])
    assert enriched["objects_info"]["init_info"]["chocolate_bar_231"]["args"]["model"] == "dxnisi"
    assert "chocolate_bar_987" not in enriched["objects_info"]["init_info"]
    assert config_path.read_bytes() == config_before
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_ambiguous_sampled_identity_fails_before_freezing(sampling_session):
    session, _, instance_dir, prefix, bindings = sampling_session
    key_path = instance_dir.parent / f"{prefix}_0_0_template.json"
    template = template_snapshot(
        {**bindings, "chocolate_bar.n.01_2": "chocolate_bar_232"},
        {"chocolate_bar_231": ("chocolate_bar", "dxnisi"), "chocolate_bar_232": ("chocolate_bar", "dxnisi")},
    )
    key_path.write_text(json.dumps(template))
    (instance_dir / f"{prefix}_0_1_template-tro_state.json").write_text("{}")

    with pytest.raises(ValueError, match="Ambiguous inserted model"):
        assign_variants(session)

    assert not session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json").exists()
    assert not session.paths.resolve(f"sessions/{session.session_id}/tro").exists()


def test_wrong_model_is_not_shared_across_tasks(sampling_session):
    session, _, instance_dir, prefix, bindings = sampling_session
    key_path = instance_dir.parent / f"{prefix}_0_0_template.json"
    key_path.write_text(json.dumps(template_snapshot(bindings, {"chocolate_bar_231": ("chocolate_bar", "amutpr")})))

    with pytest.raises(ValueError, match="Different inserted models use the same sampled name"):
        _shared_task_bindings(session, instance_dir.parent)


@pytest.fixture
def multikey_sampling_session(sampling_session):
    original_session, config_path, _, _, _ = sampling_session
    config = yaml.safe_load(Path("memory_bench/configs/mb_retrieve_target_mug_after_relocation.yaml").read_text())
    config["paths"] = original_session.config["paths"]
    config_path.write_text(yaml.safe_dump(config))
    session = Session.load(config_path)
    json_dir = session.paths.data_root / "memory-bench-task-instances/scenes/house_single_floor/json"
    json_dir.mkdir(parents=True)
    base = {
        "countertop.n.01_1": "bar_udatjt_0",
        **{f"cabinet.n.01_{index + 1}": f"top_cabinet_lkxmne_{index}" for index in range(3)},
    }
    (json_dir / "house_single_floor_stable.json").write_text(json.dumps(template_snapshot(base)))
    return session, config_path, json_dir, base


def test_two_mug_variants_resolve_model_identity_across_tasks(multikey_sampling_session):
    session, config_path, json_dir, base = multikey_sampling_session
    config_before = config_path.read_bytes()
    sampled_mugs = {
        "key_1": [("mug_231", "ntgftr"), ("mug_232", "yxaapv")],
        "key_2": [("mug_501", "yxaapv"), ("mug_500", "ntgftr")],
        "query": [("mug_231", "yxaapv"), ("mug_232", "ntgftr")],
    }
    for segment_id, mugs in sampled_mugs.items():
        activity = session.segments[segment_id]["activity"]
        bindings = {**base, **{f"mug.n.04_{index + 1}": name for index, (name, _) in enumerate(mugs)}}
        template = template_snapshot(bindings, {name: ("mug", model) for name, model in mugs})
        (json_dir / f"house_single_floor_task_{activity}_0_0_template.json").write_text(json.dumps(template))
    originals = {path: path.read_bytes() for path in json_dir.glob("*.json")}
    prefix = f"house_single_floor_task_{session.task.key_activities[0]}"
    instance_dir = json_dir / f"{prefix}_instances"
    instance_dir.mkdir()
    first_bindings = {**base, "mug.n.04_1": "mug_231", "mug.n.04_2": "mug_232"}
    for index in range(1, 5):
        (instance_dir / f"{prefix}_0_{index}_template-tro_state.json").write_text(
            json.dumps({instance: {"object_name": name} for instance, name in first_bindings.items()})
        )

    assign_variants(session)

    manifest = json.loads(session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json").read_text())
    assert manifest["asset_requirements"]["target_mug"] == ["mug_231", "mug_232"]
    variant_objects = {"tall_mug": "mug_231", "short_mug": "mug_232"}
    for entry in manifest["instances"]:
        for segment_id in sampled_mugs:
            activity = session.segments[segment_id]["activity"]
            overrides = session.task.binding_overrides(entry["case_id"], activity)
            for instance, alias in overrides.items():
                assert entry["bindings"][segment_id][instance] == variant_objects.get(alias, alias)
        tro = _remap_tro(json.loads(Path(entry["source_tro"]).read_text()), first_bindings, entry["bindings"]["key_1"])
        for instance, name in tro["inst_to_name"].items():
            assert tro[instance]["object_name"] == name
    stable = json.loads((json_dir / "house_single_floor_stable.json").read_text())
    enriched, _ = _enrich_stable_scene(session, manifest["instances"][0], stable, manifest["asset_sources"])
    assert enriched["objects_info"]["init_info"]["mug_231"]["args"]["model"] == "ntgftr"
    assert enriched["objects_info"]["init_info"]["mug_232"]["args"]["model"] == "yxaapv"
    assert config_path.read_bytes() == config_before
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_query_scoped_requirements_and_second_key_asset_source(multikey_sampling_session):
    session, _, json_dir, base = multikey_sampling_session
    first_bindings = {**base, "mug.n.04_1": "mug_231", "mug.n.04_2": "mug_232"}
    first = template_snapshot(first_bindings, {"mug_231": ("mug", "ntgftr"), "mug_232": ("mug", "yxaapv")})
    # A shared object may get its initializer from a later Key's template.
    del first["objects_info"]["init_info"]["mug_232"]
    del first["state"]["registry"]["object_registry"]["mug_232"]
    second = template_snapshot(first_bindings, {"mug_231": ("mug", "ntgftr"), "mug_232": ("mug", "yxaapv")})
    query_bindings = {**base, "mug.n.04_1": "mug_800", "mug.n.04_2": "mug_801"}
    query = template_snapshot(query_bindings, {"mug_800": ("mug", "ntgftr"), "mug_801": ("mug", "yxaapv")})
    for activity, template in zip((*session.task.key_activities, session.task.query_activity), (first, second, query)):
        (json_dir / f"house_single_floor_task_{activity}_0_0_template.json").write_text(json.dumps(template))
    prefix = f"house_single_floor_task_{session.task.key_activities[0]}"
    instance_dir = json_dir / f"{prefix}_instances"
    instance_dir.mkdir()
    (instance_dir / f"{prefix}_0_1_template-tro_state.json").write_text(
        json.dumps({instance: {"object_name": name} for instance, name in first_bindings.items()})
    )

    assign_variants(session)

    manifest = json.loads(session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json").read_text())
    requirements = manifest["asset_requirements"]
    assert requirements["top_cabinets"] == [f"top_cabinet_lkxmne_{index}" for index in range(3)]
    assert requirements["target_mug"] == ["mug_231", "mug_232"]
    assert manifest["asset_sources"]["mug_232"] == "key_2"
    stable = json.loads((json_dir / "house_single_floor_stable.json").read_text())
    enriched, _ = _enrich_stable_scene(session, manifest["instances"][0], stable, manifest["asset_sources"])
    assert enriched["objects_info"]["init_info"]["mug_232"]["args"]["model"] == "yxaapv"


def test_unknown_query_role_fails_before_freezing(sampling_session):
    session, _, instance_dir, prefix, _ = sampling_session
    session.config["assets"]["requirements"]["chocolate_bar"]["instances"] = ["chocolate_bar.n.01_99"]
    (instance_dir / f"{prefix}_0_1_template-tro_state.json").write_text("{}")

    with pytest.raises(KeyError, match="chocolate_bar.n.01_99"):
        assign_variants(session)

    assert not session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json").exists()
    assert not session.paths.resolve(f"sessions/{session.session_id}/tro").exists()


def test_assignments_keep_source_references_without_content_checks(sampling_session):
    session, _, instance_dir, prefix, _ = sampling_session
    source = instance_dir / f"{prefix}_0_1_template-tro_state.json"
    source.write_text("{}")
    assign_variants(session)
    manifest_path = session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json")
    assignments = json.loads(manifest_path.read_text())
    validate_assignments(session, assignments)
    source.write_text('{"changed": true}')
    validate_assignments(session, assignments)
    assert str(source.resolve()) in assignments["inputs"]
    assignments["inputs"] = {path: "unused legacy digest" for path in reversed(assignments["inputs"])}
    manifest_path.write_text(json.dumps(assignments))
    before = manifest_path.read_bytes()
    assign_variants(session)
    assert manifest_path.read_bytes() == before


def test_shared_scene_protects_resolved_assets(sampling_session, monkeypatch):
    session, _, instance_dir, prefix, _ = sampling_session
    (instance_dir / f"{prefix}_0_1_template-tro_state.json").write_text("{}")
    assign_variants(session)
    assignments = json.loads(session.paths.resolve(f"sessions/{session.session_id}/case_assignments.json").read_text())
    stable = json.loads((instance_dir.parent / "house_double_floor_lower_stable.json").read_text())
    enriched, _ = _enrich_stable_scene(session, assignments["instances"][0], stable, assignments["asset_sources"])
    intervents = {}
    for key in ("intervent_1", "intervent_2"):
        name = f"intervent_fixture_{key[-1]}"
        source = instance_dir / f"{key}.json"
        source.write_text(json.dumps(template_snapshot({"fixture.n.01_1": name})))
        intervents[key] = {"objects": {"used": [name]}, "entry": json.loads(source.read_text())}
    scene, assets = assemble_scene_assets(session, assignments, enriched, intervents)
    assert assets["requirements"]["chocolate_bar"] == ["chocolate_bar_231"]
    assert assets["assets"]["chocolate_bar_231"]["source"] == "insert"
    assert "intervent_fixture_1" in scene["objects_info"]["init_info"]
    intervents["intervent_1"]["objects"]["used"] = ["chocolate_bar_231"]
    with pytest.raises(ValueError, match="overlap protected"):
        assemble_scene_assets(session, assignments, enriched, intervents)


@pytest.fixture
def simulated_preparation(sampling_session, monkeypatch):
    """Exercise the complete preparation loop with a deterministic simulator substitute."""
    import sys
    from types import SimpleNamespace

    import torch as th

    from memory_bench.collection import sample_instances as sampling
    from memory_bench.utils.scene_state import read_json, write_json
    from memory_bench.tests.test_pipeline import freeze_intervents, snapshot, canonical

    session, _, instance_dir, prefix, bindings = sampling_session
    pose = {"position": [0, 0, 0], "orientation": [0, 0, 0, 1]}
    # Later Keys/Query need object templates, but no independent robot pose.
    for path in instance_dir.parent.glob(f"{prefix}_0_0_template.json"):
        template = read_json(path)
        template["metadata"]["task"]["robot_poses"] = {"robot": [pose]}
        path.write_text(json.dumps(template))
    stable_path = instance_dir.parent / "house_double_floor_lower_stable.json"
    stable = read_json(stable_path)
    stable["objects_info"]["init_info"]["unrelated"] = {"args": {"category": "fixture", "model": "other"}}
    stable["state"]["registry"]["object_registry"]["unrelated"] = {"settled": 0}
    stable["objects_info"]["init_info"]["door_fixture"] = {"args": {"category": "door", "model": "test"}}
    stable["state"]["registry"]["object_registry"]["door_fixture"] = {"open": False}
    stable_path.write_text(json.dumps(stable))
    for index in (1, 2, 3):
        source = {instance: {"object_name": name} for instance, name in bindings.items()}
        source["robot_poses"] = {"robot": [{**pose, "position": [index * 0.1, 0, 0]}]}
        (instance_dir / f"{prefix}_0_{index}_template-tro_state.json").write_text(json.dumps(source))
    assign_variants(session)
    instances = {}
    for index in (1, 2, 3):
        instances[str(index)] = {}
        for key in ("intervent_1", "intervent_2"):
            name = f"intervent_fixture_{key[-1]}"
            entry = snapshot({"robot": 0, name: 0})
            entry["metadata"]["task"]["inst_to_name"] = {"fixture.n.01_1": name}
            entry["objects_info"]["init_info"][name]["args"]["in_rooms"] = [session.segments[key]["room_id"]]
            entry["state"]["registry"]["object_registry"][name]["offset"] = index
            directory = session.paths.intervent_rawdata_root / f"{key}_{index}"
            directory.mkdir(parents=True)
            source = directory / "source.hdf5"
            canonical(source, entry, entry, session.segments[key]["activity"])
            write_json(directory / "entry.json", entry)
            write_json(directory / "exit.json", entry)
            manifest = {
                "identity": {"id": key}, "interface": {},
                "task": {"role": "intervent_task"},
                "source": {"hdf5": str(source), "demo": 0},
                "states": {"directory": str(directory), "entry": "entry.json", "exit": "exit.json"},
                "objects": {"used": [name]},
            }
            write_json(directory / "segment.json", manifest)
            instances[str(index)][key] = session.paths.relative(directory / "segment.json")
    freeze_intervents(session, instances)

    class Robot:
        name = "robot"

        def __init__(self, scene):
            self.scene = scene
            self.position = [0, 0, 0]

        def reset(self):
            raise AssertionError("Preparation must not apply the teleop reset pose")

        def set_position_orientation(self, position, orientation, **kwargs):
            self.position = position
            self.scene.snapshot["state"]["registry"]["object_registry"][self.name] = {
                "position": list(position), "orientation": list(orientation), "joint_positions": [0.0],
            }

        def get_joint_positions(self):
            return th.tensor([0.0])

        def get_position_orientation(self):
            return th.tensor(self.position, dtype=th.float64), th.tensor([0, 0, 0, 1])

    class Scene:
        def __init__(self, snapshot):
            self.snapshot = deepcopy(snapshot)
            self.snapshot["init_info"] = {"args": {"scene_file": deepcopy(snapshot)}}
            self.snapshot["objects_info"]["init_info"]["robot"] = {"args": {"model": "r1pro"}}
            self.snapshot["state"]["registry"]["object_registry"]["robot"] = {"position": [0, 0, 0]}
            self.robots = [Robot(self)]
            self.trav_map = SimpleNamespace(floor_heights=[0])
            self._seg_map = SimpleNamespace(get_room_instance_by_point=lambda point: "kitchen_0")

        def save(self, **kwargs):
            return deepcopy(self.snapshot)

        def dump_state(self, **kwargs):
            return deepcopy(self.snapshot["state"])

        def load_state(self, state, **kwargs):
            self.snapshot["state"] = deepcopy(state)

        def restore(self, snapshot, **kwargs):
            self.snapshot = deepcopy(snapshot)

        def write_task_metadata(self, *, key, data):
            self.snapshot["metadata"]["task"][key] = deepcopy(data)

        def object_registry(self, field, name):
            def load(state, **kwargs):
                self.snapshot["state"]["registry"]["object_registry"][name] = deepcopy(state)
            return SimpleNamespace(load_state=load)

    created, exit_codes = [], []

    def environment(*, configs):
        scene = Scene(configs["scene"]["scene_file"])
        env = SimpleNamespace(scene=scene, task=SimpleNamespace(), close=lambda: None)
        created.append(env)
        return env

    def set_activity(env, activity, mapping):
        env.scene.write_task_metadata(key="inst_to_name", data=mapping)

        class Object:
            def __init__(self, name):
                self.name = name

            def load_state(self, state, **kwargs):
                env.scene.snapshot["state"]["registry"]["object_registry"][self.name] = deepcopy(state)

        env.task.object_scopes = [{role: Object(name) for role, name in mapping.items()}]
        env.task.update_bddl_scope_metadata = lambda *args: None

    def settle(env):
        env.scene.snapshot["state"]["registry"]["object_registry"]["unrelated"]["settled"] += 1

    monkeypatch.setitem(sys.modules, "omnigibson", SimpleNamespace(
        Environment=environment,
        app=SimpleNamespace(_app=SimpleNamespace(post_quit=exit_codes.append)),
        shutdown=lambda: None,
    ))
    monkeypatch.setitem(sys.modules, "omnigibson.macros", SimpleNamespace(gm=SimpleNamespace()))
    monkeypatch.setitem(sys.modules, "omnigibson.utils.python_utils", SimpleNamespace(
        recursively_convert_to_torch=deepcopy,
    ))
    monkeypatch.setattr(sampling, "_set_activity", set_activity)
    monkeypatch.setattr(sampling, "_settle", settle)
    monkeypatch.setattr(sampling, "_initial_conditions", lambda task: {"satisfied": [], "unsatisfied": []})
    return session, sampling, created, exit_codes


def test_sampling_configures_simulator_before_recording_decode(simulated_preparation, monkeypatch):
    import sys
    from types import SimpleNamespace

    session, sampling, created, _ = simulated_preparation

    class Settings(SimpleNamespace):
        def __setattr__(self, name, value):
            if self.__dict__.get("locked", False) and name != "locked":
                raise RuntimeError("Simulator settings are locked after launch")
            super().__setattr__(name, value)

    settings = Settings()
    original = sampling.FrameReader

    def launch_reader(*args, **kwargs):
        settings.locked = True
        return original(*args, **kwargs)

    monkeypatch.setitem(sys.modules, "omnigibson.macros", SimpleNamespace(gm=settings))
    monkeypatch.setattr(sampling, "FrameReader", launch_reader)
    _, tro_root = sampling.sample_instances(session)
    assert settings.HEADLESS is True
    assert len(created) == 1 and len(list(tro_root.glob("*.json"))) == 3


def test_sampling_writes_only_shared_template_and_final_tros(simulated_preparation):
    from memory_bench.utils.instance_scene import load_instance_scene
    from memory_bench.utils.scene_state import read_json, scene_objects

    session, sampling, created, _ = simulated_preparation
    template_path, tro_root = sampling.sample_instances(session)
    root = template_path.parent
    assert {path.name for path in root.iterdir()} == {
        "case_assignments.json", "scene_template.json", "tro", "assets.json", "intervent_assignments.json"
    }
    assert len(list(tro_root.glob("*.json"))) == 3
    shared_positions = set()
    for path in sorted(tro_root.glob("*.json")):
        record, scene = load_instance_scene(session, path)
        assert record["validation"] == {"first_key_initial_predicates": {"satisfied": [], "unsatisfied": []}}
        assert "key_entries" not in record and "query_start" not in record
        assert record["robot"] == scene["robot"]
        assert record["robot"]["position"] == [record["instance_id"] * 0.1, 0, 0]
        assert set(record["robot"]) == {"position", "orientation"}
        assert "robot" not in scene_objects(record) and "robot" not in scene_objects(scene)
        assert "robot" not in scene["objects_info"]["init_info"]
        raw = read_json(path)
        assert not {"bindings", "case_id", "assignments", "seed", "status", "task_metadata"} & raw.keys()
        assert scene["metadata"]["task"]["inst_to_name"] == record["bindings"]["key_1"]
        shared_positions.add(tuple(record["robot"]["position"]))
        assert scene_objects(scene)["unrelated"]["settled"] == 3
        assert scene_objects(scene)["door_fixture"]["open"] is False
        assert "unrelated" in scene_objects(record)
        assert "door_fixture" not in scene_objects(record)
        for name in ("intervent_fixture_1", "intervent_fixture_2"):
            assert scene_objects(scene)[name]["offset"] == record["instance_id"]
        for role, name in record["bindings"]["key_1"].items():
            assert scene_objects(scene)[name]["object_name"] == name
    assert len(shared_positions) == 3
    assert scene_objects(read_json(template_path))["unrelated"]["settled"] == 1
    assert scene_objects(read_json(template_path))["door_fixture"]["open"] is False
    assert len(created) == 1
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.rglob("*.json")}
    assert sampling.sample_instances(session) == (template_path, tro_root)
    assert len(created) == 1
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == saved for path, saved in before.items())


def test_sampling_resumes_without_rewriting_completed_tros(simulated_preparation, monkeypatch):
    from memory_bench.utils.scene_state import read_json

    session, sampling, created, exit_codes = simulated_preparation
    original_write = sampling._write_prepared_json

    def interrupt(path, value):
        if path.name == "instance_0002.json":
            raise OSError("Simulated interruption")
        original_write(path, value)

    monkeypatch.setattr(sampling, "_write_prepared_json", interrupt)
    with pytest.raises(OSError, match="Simulated interruption"):
        sampling.sample_instances(session)
    assert exit_codes == [1]
    root = session.paths.resolve(f"sessions/{session.session_id}")
    first = root / "tro/instance_0001.json"
    saved = first.read_bytes(), first.stat().st_mtime_ns
    assert not (root / "tro/instance_0002.json").exists()
    monkeypatch.setattr(sampling, "_write_prepared_json", original_write)
    sampling.sample_instances(session)
    assert (first.read_bytes(), first.stat().st_mtime_ns) == saved
    assert len(list((root / "tro").glob("*.json"))) == 3
    assert len(created) == 2
    assignments = read_json(root / "case_assignments.json")
    assert assignments["instances"][0]["instance_id"] == read_json(first)["instance_id"]


def test_failed_initial_predicates_do_not_publish_tro(simulated_preparation, monkeypatch):
    session, sampling, _, exit_codes = simulated_preparation

    def fail(task):
        raise ValueError("Initial predicates failed")

    monkeypatch.setattr(sampling, "_initial_conditions", fail)
    with pytest.raises(ValueError, match="Initial predicates failed"):
        sampling.sample_instances(session)
    assert exit_codes == [1]
    assert not session.paths.resolve(f"sessions/{session.session_id}/tro").exists()


def test_completed_sampling_reuses_reformatted_template(simulated_preparation):
    session, sampling, created, _ = simulated_preparation
    template_path, _ = sampling.sample_instances(session)
    template_path.write_text(template_path.read_text() + "\n")
    assert sampling.sample_instances(session)[0] == template_path
    assert len(created) == 1


def test_sampling_and_collection_reuse_reformatted_intervent_allocation(simulated_preparation):
    from memory_bench.utils.instance_scene import load_instance_scene

    session, sampling, created, _ = simulated_preparation
    template_path, tro_root = sampling.sample_instances(session)
    allocation_path = template_path.parent / "intervent_assignments.json"
    allocation_path.write_text(allocation_path.read_text() + "\n")
    assert sampling.sample_instances(session) == (template_path, tro_root)
    assert load_instance_scene(session, tro_root / "instance_0001.json")[0]["instance_id"] == 1
    assert len(created) == 1


def test_sampling_records_paths_without_content_digests(simulated_preparation):
    session, sampling, _, _ = simulated_preparation
    template_path, tro_root = sampling.sample_instances(session)
    template = json.loads(template_path.read_text())
    assert set(template["metadata"]["task"]["memory_bench_inputs"]) == {"assignments", "intervents"}
    assert set(template["metadata"]["task"]["memory_bench_assets"]) == {"path"}
    tro = json.loads((tro_root / "instance_0001.json").read_text())
    assert set(tro) == {"session_id", "instance_id", "scene_template", "robot", "validation", "state"}


def test_collection_does_not_require_processing_artifacts(simulated_preparation):
    from memory_bench.collection.collect_session import SessionCollector

    session, sampling, _, _ = simulated_preparation
    template, tro_root = sampling.sample_instances(session)
    SessionCollector(session, tro_root / "instance_0001.json", session.paths.output_root / "recordings")
    assert not (session.paths.output_root / "segments").exists()
    assert not list(session.paths.output_root.rglob("*normalized*"))


def test_collector_restores_template_and_tro_without_scene_copy(simulated_preparation):
    from memory_bench.collection.collect_session import SessionCollector
    from memory_bench.utils.instance_scene import load_instance_scene

    session, sampling, created, _ = simulated_preparation
    template, tro_root = sampling.sample_instances(session)
    tro_path = tro_root / "instance_0001.json"
    _, expected_scene = load_instance_scene(session, tro_path)
    recording = template.parent / "recordings"
    collector = SessionCollector(session, tro_path, recording)
    live_scene = created[0].scene
    robot_state = deepcopy(live_scene.snapshot["state"]["registry"]["object_registry"]["robot"])
    robot_info = deepcopy(live_scene.snapshot["objects_info"]["init_info"]["robot"])
    collector.restore_initial_scene(live_scene)
    expected_scene["objects_info"]["init_info"]["robot"] = robot_info
    expected_scene["state"]["registry"]["object_registry"]["robot"] = robot_state
    assert created[0].scene.snapshot == expected_scene
    assert "robot" not in collector.scene_file["objects_info"]["init_info"]
    assert not recording.exists()
    assert not list(template.parent.rglob("scene.json"))
