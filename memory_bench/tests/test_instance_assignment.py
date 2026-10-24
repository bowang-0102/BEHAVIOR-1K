from collections import Counter
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import random
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import h5py
import pytest
import yaml

from memory_bench.collection.collect_session import collect_session
from memory_bench.collection import prepare_session
from memory_bench.collection.prepare_session import (
    _shared_task_bindings,
    assemble_scene_assets,
    assign_variants,
    init,
    sample_tasks,
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
def sampling_session(tmp_path):
    config = yaml.safe_load(Path("memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml").read_text())
    config["paths"] = {
        "data_root": str(tmp_path / "data"),
        "intervent_rawdata_root": str(tmp_path / "raw"),
        "output_root": str(tmp_path / "output"),
    }
    config_path = tmp_path / "session.yaml"
    config_path.write_text(yaml.safe_dump(config))
    session = Session.load(config_path)
    json_dir = session.paths.scene_json_dir
    json_dir.mkdir(parents=True)
    bindings = {
        "chocolate_bar.n.01_1": "chocolate_bar_231",
        "countertop.n.01_1": "bar_egwapq_0",
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

    assignment_path = session.paths.session_dir / "variant_assignments.json"
    saved = assignment_path.read_text()
    manifest = json.loads(saved)
    assert manifest["asset_requirements"]["chocolate_bar"] == ["chocolate_bar_231"]
    assert manifest["asset_sources"]["chocolate_bar_231"] == "key_1"
    assert manifest["count"] == len(source_ids)
    assert [entry["source_task_instance_id"] for entry in manifest["instances"]] == sorted(source_ids)
    assert [entry["instance_id"] for entry in manifest["instances"]] == list(range(1, len(source_ids) + 1))
    counts = Counter(entry["variant_id"] for entry in manifest["instances"])
    assert max(counts.values()) - min(counts.values()) <= 1
    assert all(path.read_text() == original for path, original in originals.items())
    assert session.config["instance_sampling"]["count"] == 150
    assert session.instance_count == len(source_ids)
    reloaded = Session.load(config_path)
    assert reloaded.config["instance_sampling"]["count"] == 150
    assert reloaded.instance_count == len(source_ids)
    assert yaml.safe_load(config_path.read_text())["instance_sampling"]["count"] == 150
    assert config_path.read_bytes() == config_before
    session_root = assignment_path.parent
    frozen = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in session_root.rglob("*.json")}
    with pytest.raises(FileExistsError, match="Variant assignments already exist"):
        assign_variants(session)
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == value for path, value in frozen.items())
    assert assignment_path.read_text() == saved
    assert not (session_root / "tro").exists()
    assert not (session_root / "instances.json").exists()


def test_source_scan_records_metadata_without_modifying_recording(sampling_session, monkeypatch):
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
    path = session.paths.reports_dir / "source_inventory.json"
    assert json.loads(path.read_text()) == report
    assert source.read_bytes() == source_before


def test_assignment_rejects_an_empty_tro_directory(sampling_session):
    session, _, instance_dir, _, _ = sampling_session
    (instance_dir / "unrelated-tro_state.json").write_text("{}")
    with pytest.raises(ValueError, match="No sampled .* TRO files found"):
        assign_variants(session)
    assert not (session.paths.session_dir / "variant_assignments.json").exists()


def test_collection_requires_prepared_instance(sampling_session):
    session, _, _, _, _ = sampling_session
    with pytest.raises(FileNotFoundError, match="instance_0001.json"):
        collect_session(session)


@pytest.mark.parametrize("instance_count", [None, 2])
@pytest.mark.parametrize("seed", [None, 0, 42])
def test_sampler_uses_config_and_assignment_uses_actual_outputs(sampling_session, monkeypatch, instance_count, seed):
    session, config_path, instance_dir, prefix, _ = sampling_session
    session.instance_sampling.update(count=5, seed=17)
    config_path.write_text(yaml.safe_dump(session.config))
    original_config = config_path.read_bytes()
    source_ids = (1, 4, 9) if instance_count is None else (1,)
    calls = []
    monkeypatch.setenv("OMNIGIBSON_DATA_PATH", "previous-data")
    monkeypatch.setenv("OMNIGIBSON_TASK_INSTANCES_DATASET", "previous-instances")

    def run(command, **kwargs):
        assert kwargs["check"] is True
        assert os.environ["OMNIGIBSON_DATA_PATH"] == str(session.paths.data_root)
        assert os.environ["OMNIGIBSON_TASK_INSTANCES_DATASET"] == str(session.paths.task_instances_dir)
        calls.append(command)
        if Path(command[1]).name == "multiply_b1k_tasks.py":
            for source_id in source_ids:
                (instance_dir / f"{prefix}_0_{source_id}_template-tro_state.json").write_text("{}")

    monkeypatch.setattr(prepare_session.subprocess, "run", run)
    argv = ["prepare_session", str(config_path), "sample"]
    if instance_count is not None:
        argv.extend(["--instance-count", str(instance_count)])
    if seed is not None:
        argv.extend(["--seed", str(seed)])
    monkeypatch.setattr(sys, "argv", argv)
    prepare_session.main()
    assert [Path(command[1]).name for command in calls] == [
        "sample_b1k_tasks.py", "multiply_b1k_tasks.py", "sample_robot_pose.py", "sample_b1k_tasks.py",
    ]
    multiply = calls[1]
    assert multiply[multiply.index("--end_idx") + 1] == str(instance_count if instance_count is not None else 5)
    assert multiply[multiply.index("--seed") + 1] == str(seed if seed is not None else 17)
    assert "--headless" in multiply
    assert all(command[command.index("--output_dir") + 1] == str(instance_dir.parent) for command in calls)
    assert calls[-1][calls[-1].index("--activity") + 1] == session.segments[session.query_id]["activity"]
    assign_variants(session)
    assert session.instance_count == len(source_ids)
    assert config_path.read_bytes() == original_config
    calls.clear()
    with pytest.raises(FileExistsError, match="Task variants already assigned"):
        sample_tasks(session)
    assert calls == []


@pytest.mark.parametrize("instance_count", [0, -1])
def test_sampler_rejects_nonpositive_cli_count_before_starting(sampling_session, monkeypatch, capsys, instance_count):
    _, config_path, _, _, _ = sampling_session
    run = Mock()
    monkeypatch.setattr(prepare_session.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", [
        "prepare_session", str(config_path), "sample", "--instance-count", str(instance_count),
    ])

    with pytest.raises(SystemExit) as error:
        prepare_session.main()

    assert error.value.code == 2
    assert "--instance-count must be positive" in capsys.readouterr().err
    run.assert_not_called()


def test_collection_startup_uses_standard_paths_and_session_frequency(sampling_session, monkeypatch):
    from memory_bench.collection import collect_session as collection

    session, _, _, _, _ = sampling_session
    monkeypatch.setenv("OMNIGIBSON_DATA_PATH", "previous-data")
    monkeypatch.setenv("OMNIGIBSON_TASK_INSTANCES_DATASET", "previous-instances")
    session.bridge["action_frequency"] = 60
    collector = SimpleNamespace(scene_file={}, recording_dir="recordings/instance_0001")
    constructor = Mock(return_value=collector)
    monkeypatch.setattr(collection, "SessionCollector", constructor)
    monkeypatch.setitem(sys.modules, "joylo.gello.utils.og_teleop_utils", SimpleNamespace(
        generate_basic_environment_config=lambda *args: {"env": {}},
    ))
    server = SimpleNamespace(serve=Mock())
    server_type = Mock(return_value=server)
    monkeypatch.setitem(sys.modules, "joylo.gello.robots.og_robot", SimpleNamespace(OGRobotServer=server_type))
    collect_session(session, host="192.0.2.1", port=6107)
    assert os.environ["OMNIGIBSON_DATA_PATH"] == str(session.paths.data_root)
    assert os.environ["OMNIGIBSON_TASK_INSTANCES_DATASET"] == "previous-instances"
    instance_path, output_path = constructor.call_args.args[1:]
    assert instance_path == session.paths.session_dir / "tro/instance_0001.json"
    assert output_path == session.paths.recordings_dir / "instance_0001"
    settings = server_type.call_args.kwargs
    assert settings["host"] == "192.0.2.1" and settings["port"] == 6107
    assert settings["config"]["env"]["action_frequency"] == 60
    assert settings["config"]["env"]["physics_frequency"] == 240
    server.serve.assert_called_once()


@pytest.mark.parametrize("configured_count", [1, 150])
@pytest.mark.parametrize("stage,instance_id,cli_instance_id", [
    ("build_episodes", None, None),
    ("build_episodes", 2, None),
    ("build_episodes", None, 2),
    ("build_episodes", 1, 3),
    ("compose_episodes", None, None),
    ("compose_episodes", 2, None),
    ("compose_episodes", None, 2),
    ("compose_episodes", 1, 3),
    ("replay_episodes", None, None),
])
def test_episode_commands_use_actual_assignment_count(
    sampling_session, monkeypatch, configured_count, stage, instance_id, cli_instance_id,
):
    session, config_path, instance_dir, prefix, _ = sampling_session
    session.config["instance_sampling"]["count"] = configured_count
    session.composition.update(instance_id=instance_id, order_seed=17)
    config_path.write_text(yaml.safe_dump(session.config))
    original_config = config_path.read_bytes()
    for source_id in (1, 4, 9):
        (instance_dir / f"{prefix}_0_{source_id}_template-tro_state.json").write_text("{}")
    assign_variants(session)
    recipe_root = session.paths.recipes_dir
    recipe_root.mkdir(parents=True)
    for recipe_instance_id in (1, 2, 3):
        recipe = {"instance_id": recipe_instance_id, "episode_id": f"episode_{recipe_instance_id - 1:08d}"}
        (recipe_root / f"{recipe['episode_id']}.json").write_text(json.dumps(recipe))
    module = importlib.import_module(f"memory_bench.episodes.{stage}")
    dispatched = []
    orders = []
    demos = []
    if stage == "build_episodes":
        def build(session, instance_id, order):
            dispatched.append(instance_id)
            orders.append(order)
            demos.append(session.composition["demo_id"])
        monkeypatch.setattr(module, "build_episode", build)
    else:
        def consume(session, recipe):
            dispatched.append(recipe["instance_id"])
        if stage == "compose_episodes":
            monkeypatch.setattr(module, "compose_episode", consume)
        else:
            monkeypatch.setattr(module, "replay_episode", consume)
    argv = [stage, str(config_path)]
    if cli_instance_id is not None:
        argv.extend(["--instance-id", str(cli_instance_id)])
        if stage == "build_episodes":
            argv.extend(["--seed", "0", "--demo-id", "2"])
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setenv("OMNIGIBSON_DATA_PATH", "previous-data")
    monkeypatch.setenv("OMNIGIBSON_TASK_INSTANCES_DATASET", "previous-instances")
    module.main()
    assert os.environ["OMNIGIBSON_DATA_PATH"] == str(session.paths.data_root)
    assert os.environ["OMNIGIBSON_TASK_INSTANCES_DATASET"] == "previous-instances"
    selected_instance = cli_instance_id if cli_instance_id is not None else instance_id
    expected_ids = [1, 2, 3] if selected_instance is None else [selected_instance]
    assert dispatched == expected_ids
    assert config_path.read_bytes() == original_config
    if stage == "build_episodes":
        rng = random.Random(0 if cli_instance_id is not None else 17)
        all_orders = [rng.choice(session.composition["task_orders"]) for _ in range(3)]
        assert orders == [all_orders[index - 1] for index in expected_ids]
        assert demos == [2 if cli_instance_id is not None else 1] * len(expected_ids)


def test_initialization_refuses_existing_session(sampling_session):
    session, config_path, instance_dir, prefix, bindings = sampling_session
    stable_path = (
        session.paths.data_root
        / "2026-challenge-task-instances/scenes/house_double_floor_lower/json/house_double_floor_lower_stable.json"
    )
    stable_path.parent.mkdir(parents=True)
    stable_path.write_bytes((instance_dir.parent / "house_double_floor_lower_stable.json").read_bytes())
    init(session)
    snapshot_path = session.paths.session_dir / "session.json"
    saved = snapshot_path.read_text()
    tro_path = instance_dir / f"{prefix}_0_1_template-tro_state.json"
    tro_path.write_text(json.dumps({instance: {"object_name": name} for instance, name in bindings.items()}))
    assign_variants(session)

    reloaded = Session.load(config_path)
    assert reloaded.instance_count == 1
    assignments_path = session.paths.session_dir / "variant_assignments.json"
    assignments_before = assignments_path.read_bytes()
    with pytest.raises(FileExistsError, match="Session already prepared"):
        init(reloaded)

    assert snapshot_path.read_text() == saved
    assert assignments_path.read_bytes() == assignments_before
    assert reloaded.instance_count == 1


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

    manifest = json.loads((session.paths.session_dir / "variant_assignments.json").read_text())
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

    assert not (session.paths.session_dir / "variant_assignments.json").exists()
    assert not (session.paths.session_dir / "tro").exists()


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
    json_dir = session.paths.scene_json_dir
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

    manifest = json.loads((session.paths.session_dir / "variant_assignments.json").read_text())
    assert manifest["asset_requirements"]["target_mug"] == ["mug_231", "mug_232"]
    variant_objects = {"tall_mug": "mug_231", "short_mug": "mug_232"}
    for entry in manifest["instances"]:
        for segment_id in sampled_mugs:
            activity = session.segments[segment_id]["activity"]
            overrides = session.task.activity_variants[entry["variant_id"]][activity]
            for instance, alias in overrides.items():
                assert entry["bindings"][segment_id][instance] == variant_objects.get(alias, alias)
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

    manifest = json.loads((session.paths.session_dir / "variant_assignments.json").read_text())
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

    assert not (session.paths.session_dir / "variant_assignments.json").exists()
    assert not (session.paths.session_dir / "tro").exists()


def test_assignments_keep_source_references_without_content_checks(sampling_session):
    session, _, instance_dir, prefix, _ = sampling_session
    source = instance_dir / f"{prefix}_0_1_template-tro_state.json"
    source.write_text("{}")
    assign_variants(session)
    manifest_path = session.paths.session_dir / "variant_assignments.json"
    assignments = json.loads(manifest_path.read_text())
    assert str(source.relative_to(session.paths.output_root)) in assignments["inputs"]
    assert assignments["instances"][0]["source_tro"] == str(source.relative_to(session.paths.output_root))


def test_shared_scene_protects_resolved_assets(sampling_session, monkeypatch):
    session, _, instance_dir, prefix, _ = sampling_session
    (instance_dir / f"{prefix}_0_1_template-tro_state.json").write_text("{}")
    assign_variants(session)
    assignments = json.loads(
        (session.paths.session_dir / "variant_assignments.json").read_text()
    )
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
            instances[str(index)][key] = str((directory / "segment.json").resolve())
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
        env = SimpleNamespace(scene=scene, close=lambda: None)
        env.task = SimpleNamespace(
            update_activity=Mock(),
            initialize_activity=initialize_activity,
            compiled_task=SimpleNamespace(check_initial_conditions=Mock(return_value=(
                True, {"satisfied": [], "unsatisfied": []},
            ))),
        )
        created.append(env)
        return env

    def initialize_activity(env):
        mapping = env.scene.snapshot["metadata"]["task"]["inst_to_name"]

        class Object:
            def __init__(self, name):
                self.name = name

            def load_state(self, state, **kwargs):
                env.scene.snapshot["state"]["registry"]["object_registry"][self.name] = deepcopy(state)

        env.task.object_scopes = [{role: Object(name) for role, name in mapping.items()}]
        env.task.update_bddl_scope_metadata = lambda *args: None
        return True, {}

    physics_steps = 0

    def step_physics():
        nonlocal physics_steps
        physics_steps += 1
        if physics_steps % 120 == 0:
            created[-1].scene.snapshot["state"]["registry"]["object_registry"]["unrelated"]["settled"] += 1

    monkeypatch.setitem(sys.modules, "omnigibson", SimpleNamespace(
        Environment=environment,
        sim=SimpleNamespace(step_physics=step_physics),
        app=SimpleNamespace(_app=SimpleNamespace(post_quit=exit_codes.append)),
        shutdown=lambda: None,
    ))
    monkeypatch.setitem(sys.modules, "omnigibson.macros", SimpleNamespace(gm=SimpleNamespace()))
    monkeypatch.setitem(sys.modules, "omnigibson.utils.python_utils", SimpleNamespace(
        recursively_convert_to_torch=deepcopy,
    ))
    return session, sampling, created, exit_codes


def test_prepared_instances_load_after_moving_output_root(simulated_preparation, tmp_path):
    import shutil
    from memory_bench.utils.instance_scene import load_instance_scene

    session, sampling, _, _ = simulated_preparation
    _, tro_root = sampling.sample_instances(session)
    tro_path = tro_root / "instance_0001.json"
    expected_instance, expected_scene = load_instance_scene(session, tro_path)
    destination = tmp_path / "relocated-memory-bench"
    shutil.move(session.paths.output_root, destination)
    config = deepcopy(session.config)
    config["paths"]["output_root"] = str(destination)
    config_path = tmp_path / "relocated.yaml"
    config_path.write_text(yaml.safe_dump(config))
    moved = Session.load(config_path)
    instance, scene = load_instance_scene(moved, moved.paths.resolve(str(tro_path.relative_to(session.paths.output_root))))
    assert instance == expected_instance and scene == expected_scene
    assert moved.paths.resolve(instance["source_tro"]).is_file()
    assert not Path(instance["source_tro"]).is_absolute()


def test_sampling_cli_configures_simulator_before_recording_decode(simulated_preparation, monkeypatch):
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
        assert os.environ["OMNIGIBSON_DATA_PATH"] == str(session.paths.data_root)
        assert os.environ["OMNIGIBSON_TASK_INSTANCES_DATASET"] == "previous-instances"
        settings.locked = True
        return original(*args, **kwargs)

    monkeypatch.setitem(sys.modules, "omnigibson.macros", SimpleNamespace(gm=settings))
    monkeypatch.setattr(sampling, "FrameReader", launch_reader)
    monkeypatch.setattr(sampling.Session, "load", lambda _: session)
    monkeypatch.setattr(sys, "argv", ["sample_instances", "session.yaml"])
    monkeypatch.setenv("OMNIGIBSON_DATA_PATH", "previous-data")
    monkeypatch.setenv("OMNIGIBSON_TASK_INSTANCES_DATASET", "previous-instances")
    sampling.main()
    tro_root = session.paths.session_dir / "tro"
    assert settings.HEADLESS is True
    assert len(created) == 1 and len(list(tro_root.glob("*.json"))) == 3


@pytest.mark.parametrize("explicit_source_bindings", [False, True])
def test_sampling_writes_only_shared_template_and_final_tros(simulated_preparation, explicit_source_bindings):
    from memory_bench.utils.instance_scene import load_instance_scene
    from memory_bench.utils.scene_state import read_json, scene_objects

    session, sampling, created, _ = simulated_preparation
    if explicit_source_bindings:
        assignments = read_json((session.paths.session_dir / "variant_assignments.json"))
        for entry in assignments["instances"]:
            source = session.paths.resolve(entry["source_tro"])
            template = read_json(source.parent.parent / (
                f"{session.scene_model}_task_{session.task.key_activities[0]}_0_0_template.json"
            ))
            bindings = dict(template["metadata"]["task"]["inst_to_name"])
            tro = read_json(source)
            first, second = "cabinet.n.01_1", "cabinet.n.01_2"
            bindings[first], bindings[second] = bindings[second], bindings[first]
            tro[first], tro[second] = tro[second], tro[first]
            tro["inst_to_name"] = bindings
            source.write_text(json.dumps(tro))
    template_path, tro_root = sampling.sample_instances(session)
    root = template_path.parent
    assert {path.name for path in root.iterdir()} == {
        "variant_assignments.json", "scene_template.json", "tro", "assets.json", "intervent_assignments.json"
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
        assert not {"bindings", "variant_id", "assignments", "seed", "status", "task_metadata"} & raw.keys()
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
    with pytest.raises(FileExistsError, match="Prepared instances already exist"):
        sampling.sample_instances(session)
    assert len(created) == 1
    assert all((path.read_bytes(), path.stat().st_mtime_ns) == saved for path, saved in before.items())


def test_sampling_failure_preserves_completed_tros(simulated_preparation, monkeypatch):
    from memory_bench.utils.scene_state import read_json

    session, sampling, created, exit_codes = simulated_preparation
    original_write = sampling.write_json_atomic

    def interrupt(path, value):
        if path.name == "instance_0002.json":
            raise OSError("Simulated interruption")
        original_write(path, value)

    monkeypatch.setattr(sampling, "write_json_atomic", interrupt)
    with pytest.raises(OSError, match="Simulated interruption"):
        sampling.sample_instances(session)
    assert exit_codes == [1]
    root = session.paths.session_dir
    first = root / "tro/instance_0001.json"
    saved = first.read_bytes(), first.stat().st_mtime_ns
    assert not (root / "tro/instance_0002.json").exists()
    with pytest.raises(FileExistsError, match="Prepared instances already exist"):
        sampling.sample_instances(session)
    assert (first.read_bytes(), first.stat().st_mtime_ns) == saved
    assert len(list((root / "tro").glob("*.json"))) == 1
    assert len(created) == 1
    assignments = read_json(root / "variant_assignments.json")
    assert assignments["instances"][0]["instance_id"] == read_json(first)["instance_id"]


def test_failed_initial_predicates_do_not_publish_tro(simulated_preparation, monkeypatch):
    session, sampling, _, exit_codes = simulated_preparation
    og = sys.modules["omnigibson"]
    environment = og.Environment

    def failing_environment(**kwargs):
        env = environment(**kwargs)
        env.task.compiled_task.check_initial_conditions.return_value = (
            False, {"satisfied": [], "unsatisfied": ["inside"]},
        )
        return env

    monkeypatch.setattr(og, "Environment", failing_environment)
    with pytest.raises(ValueError, match="First-Key initial BDDL predicates are not satisfied"):
        sampling.sample_instances(session)
    assert exit_codes == [1]
    assert not (session.paths.session_dir / "tro").exists()


def test_collection_reads_reformatted_intervent_allocation(simulated_preparation):
    from memory_bench.utils.instance_scene import load_instance_scene

    session, sampling, created, _ = simulated_preparation
    template_path, tro_root = sampling.sample_instances(session)
    allocation_path = template_path.parent / "intervent_assignments.json"
    allocation_path.write_text(allocation_path.read_text() + "\n")
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
    SessionCollector(session, tro_root / "instance_0001.json", session.paths.recordings_dir)
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
