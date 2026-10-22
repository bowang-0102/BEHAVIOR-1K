"""Benchmark session YAML is loaded once and validated."""

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


@pytest.mark.parametrize(
    "name, expected_keys",
    [
        ("mb_retrieve_chocolate_from_top_cabinet.yaml", ["key_1"]),
        ("mb_retrieve_target_mug_after_relocation.yaml", ["key_1", "key_2"]),
    ],
)
def test_configuration_remains_available_after_yaml_is_removed(session_config, name, expected_keys):
    path, config = session_config(name)
    session = Session.load(path)
    path.unlink()

    # Configuration remains available without reopening the YAML file.
    assert session.config == config
    assert session.session_id == config["session_id"]
    assert session.scene_model == config["scene_model"]
    assert session.key_ids == expected_keys


def test_collection_keys_follow_numeric_order_independent_of_yaml_order(session_config):
    path, config = session_config("mb_retrieve_target_mug_after_relocation.yaml")
    segments = config["segments"]
    segments["key_10"] = {**segments["key_2"], "depends_on": ["key_2"]}
    segments["query"]["depends_on"].append("key_10")
    for order in config["composition"]["task_orders"]:
        order.insert(-1, "key_10")
    config["segments"] = {key: segments[key] for key in reversed(segments)}
    path.write_text(yaml.safe_dump(config, sort_keys=False))

    assert Session.load(path).key_ids == ["key_1", "key_2", "key_10"]


def test_path_activation_uses_config_without_changing_runtime(session_config, monkeypatch):
    path, config = session_config("mb_retrieve_chocolate_from_top_cabinet.yaml")
    for name in ("OMNIGIBSON_GPU_ID", "OMNIGIBSON_HEADLESS", "OMNIGIBSON_TASK_INSTANCES_DATASET",
                 "OMNIGIBSON_DATA_PATH"):
        monkeypatch.setenv(name, "previous-value")
    session = Session.load(path)
    session.paths.activate()
    import os

    assert os.environ["OMNIGIBSON_GPU_ID"] == "previous-value"
    assert os.environ["OMNIGIBSON_HEADLESS"] == "previous-value"
    assert os.environ["OMNIGIBSON_DATA_PATH"] == str(session.paths.data_root)
    assert os.environ["OMNIGIBSON_TASK_INSTANCES_DATASET"] == str(session.paths.output_root / "memory-bench-task-instances")
    assert session.config == config


def test_benchmark_configuration_still_requires_query(session_config):
    path, config = session_config("mb_retrieve_chocolate_from_top_cabinet.yaml")
    del config["segments"]["query"]
    path.write_text(yaml.safe_dump(config))

    with pytest.raises(ValueError, match="exactly one Query"):
        Session.load(path)


@pytest.mark.parametrize(
    "field",
    [
        "unexpected_option",
        "collection.advance_segment_key",
        "collection.action_frequency",
        "bridge.stop_pose",
        "collection.unused_option",
        "validation",
        "assets.requirements.chocolate_bar.appearance",
        "segments.intervent_1.instruction",
    ],
)
def test_extra_options_are_preserved_without_a_field_whitelist(session_config, field):
    path, config = session_config("mb_retrieve_chocolate_from_top_cabinet.yaml")
    parts = field.split(".")
    section = config
    for key in parts[:-1]:
        section = section.setdefault(key, {})
    section[parts[-1]] = "unused"
    path.write_text(yaml.safe_dump(config))

    assert Session.load(path).config == config


def test_shared_frequency_and_independent_variant_seed(session_config):
    path, config = session_config("mb_retrieve_chocolate_from_top_cabinet.yaml")
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


@pytest.mark.parametrize("frequency", [0, -1])
def test_nonpositive_action_frequency_is_rejected(session_config, frequency):
    path, config = session_config("mb_retrieve_chocolate_from_top_cabinet.yaml")
    config["bridge"]["action_frequency"] = frequency
    path.write_text(yaml.safe_dump(config))

    with pytest.raises(ValueError, match="bridge.action_frequency must be positive"):
        Session.load(path)


