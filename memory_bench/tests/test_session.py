import os
from pathlib import Path

import pytest
import yaml

from memory_bench.session import Session


@pytest.fixture
def session_config(tmp_path, monkeypatch):
    monkeypatch.delenv("OMNIGIBSON_DATA_PATH", raising=False)

    def write_config(name):
        config = yaml.safe_load((Path("memory_bench/configs") / name).read_text())
        config["paths"] = {key: str(tmp_path) for key in config["paths"]}
        path = tmp_path / name
        path.write_text(yaml.safe_dump(config))
        return path, config

    return write_config


def test_configuration_remains_available_after_yaml_is_removed(session_config):
    path, config = session_config("mb000_retrieve_bottle_of_milk.yaml")
    session = Session.load(path)
    path.unlink()

    # Configuration remains available without reopening the YAML file.
    assert session.config == config
    assert session.session_id == config["session_id"]
    assert session.scene_model == config["scene_model"]
    assert session.key_ids == ["key_1"]


@pytest.mark.parametrize("config_name", [
    path.name for path in sorted(Path("memory_bench/configs").glob("*.yaml"))
])
def test_numbered_session_resolves_its_bddl_query_and_output_paths(session_config, config_name):
    path, config = session_config(config_name)
    session = Session.load(path)
    query_activity = session.segments[session.query_id]["activity"]

    assert session.session_id == path.stem == session.task.task_name
    assert session.session_id != query_activity
    assert session.task.query_activity == query_activity
    assert session.paths.session_dir.name == session.session_id
    assert session.paths.recordings_dir.name == session.session_id
    for variant_id in session.task.variants:
        assert session.task.instruction_for(query_activity, variant_id) == session.task.query_instruction


def test_query_activity_must_match_the_registered_session_task(session_config):
    path, config = session_config("mb000_retrieve_bottle_of_milk.yaml")
    config["segments"]["query"]["activity"] = "mb_return_book_to_original_bookcase"
    path.write_text(yaml.safe_dump(config))

    with pytest.raises(ValueError, match="Query activity must match the registered task"):
        Session.load(path)


def test_collection_keys_follow_numeric_order_independent_of_yaml_order(session_config):
    path, config = session_config("mb000_retrieve_bottle_of_milk.yaml")
    config["benchmark"]["level"] = 2
    segments = config["segments"]
    segments["key_2"] = {**segments["key_1"], "depends_on": ["key_1"]}
    segments["key_10"] = {**segments["key_2"], "depends_on": ["key_2"]}
    segments["query"]["depends_on"].extend(["key_2", "key_10"])
    for order in config["composition"]["task_orders"]:
        order[-1:-1] = ["key_2", "key_10"]
    config["segments"] = {key: segments[key] for key in reversed(segments)}
    path.write_text(yaml.safe_dump(config, sort_keys=False))

    assert Session.load(path).key_ids == ["key_1", "key_2", "key_10"]


def test_path_construction_has_no_environment_or_directory_side_effects(session_config, monkeypatch):
    path, config = session_config("mb000_retrieve_bottle_of_milk.yaml")
    config["paths"] = {key: str(path.parent / key / ".." / f"session_{key}") for key in config["paths"]}
    path.write_text(yaml.safe_dump(config))
    for name in ("OMNIGIBSON_GPU_ID", "OMNIGIBSON_HEADLESS", "OMNIGIBSON_TASK_INSTANCES_DATASET",
                 "OMNIGIBSON_DATA_PATH"):
        monkeypatch.setenv(name, "previous-value")
    session = Session.load(path)

    assert os.environ["OMNIGIBSON_GPU_ID"] == "previous-value"
    assert os.environ["OMNIGIBSON_HEADLESS"] == "previous-value"
    assert os.environ["OMNIGIBSON_DATA_PATH"] == "previous-value"
    assert os.environ["OMNIGIBSON_TASK_INSTANCES_DATASET"] == "previous-value"
    for key, value in config["paths"].items():
        assert getattr(session.paths, key) == Path(value).resolve()
        assert not getattr(session.paths, key).exists()
    assert session.config == config


def test_benchmark_configuration_still_requires_query(session_config):
    path, config = session_config("mb000_retrieve_bottle_of_milk.yaml")
    del config["segments"]["query"]
    path.write_text(yaml.safe_dump(config))

    with pytest.raises(ValueError, match="exactly one Query"):
        Session.load(path)


@pytest.mark.parametrize(
    "field",
    [
        "unexpected_option",
        "collection.action_frequency",
        "segments.intervent_1.instruction",
    ],
)
def test_extra_options_are_preserved_without_a_field_whitelist(session_config, field):
    path, config = session_config("mb000_retrieve_bottle_of_milk.yaml")
    parts = field.split(".")
    section = config
    for key in parts[:-1]:
        section = section.setdefault(key, {})
    section[parts[-1]] = "unused"
    path.write_text(yaml.safe_dump(config))

    assert Session.load(path).config == config


def test_shared_frequency_and_independent_variant_seed(session_config):
    path, config = session_config("mb000_retrieve_bottle_of_milk.yaml")
    config["bridge"]["action_frequency"] = 60
    path.write_text(yaml.safe_dump(config))
    session = Session.load(path)
    assert session.action_frequency == 60
    assert (
        session.variant_sampling.get("seed", session.instance_sampling["seed"])
        == config["instance_sampling"]["seed"]
    )

    config["variant_sampling"] = {"seed": 19}
    path.write_text(yaml.safe_dump(config))
    session = Session.load(path)
    assert session.variant_sampling["seed"] == 19
    assert session.instance_sampling["seed"] == config["instance_sampling"]["seed"]
