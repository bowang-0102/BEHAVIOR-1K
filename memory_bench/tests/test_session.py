"""Session YAML is loaded once and validated for the selected workflow."""

from pathlib import Path

import pytest
import yaml

from memory_bench.session import Session


@pytest.fixture
def session_config(tmp_path, monkeypatch):
    monkeypatch.delenv("OMNIGIBSON_DATA_PATH", raising=False)
    monkeypatch.delenv("MB_ROOT", raising=False)

    def write_config(name):
        template = "mb_retrieve_chocolate_from_top_cabinet.yaml" if name == "debug_intervents.yaml" else name
        config = yaml.safe_load((Path("memory_bench/configs") / template).read_text())
        if name == "debug_intervents.yaml":
            config.update(mode="intervent_debug", session_id="debug_test", full_scene="scene.json", open_doorways=[])
            config["segments"] = {key: value for key, value in config["segments"].items() if value["role"] == "intervent_task"}
            config["task_order"] = list(config["segments"])
            config["recordings"] = {key: {"hdf5": f"task-{value['source']['task_id']:04d}/episode.hdf5", "demo": 0}
                                    for key, value in config["segments"].items()}
        config["paths"] = {key: str(tmp_path) for key in config["paths"]}
        path = tmp_path / name
        path.write_text(yaml.safe_dump(config))
        return path, config

    return write_config


@pytest.mark.parametrize(
    "name",
    [
        "mb_retrieve_chocolate_from_top_cabinet.yaml",
        "mb_retrieve_target_mug_after_relocation.yaml",
        "debug_intervents.yaml",
    ],
)
def test_configuration_remains_available_after_yaml_is_removed(session_config, name):
    path, config = session_config(name)
    session = Session.load(path)
    path.unlink()

    # Every YAML group remains accessible through Session without reopening the file.
    for key, value in config.items():
        if key != "paths":
            assert getattr(session, key) == value
    assert session.config == config
    if session.mode == "intervent_debug":
        assert session.task_order == ["intervent_1", "intervent_2"]
    else:
        assert session.instance_count == config["instance_sampling"]["count"]
        assert session.key_ids == config["collection"]["key_segment_ids"]


@pytest.mark.parametrize("invalid", ["order", "role", "dependency", "room"])
def test_debug_configuration_is_validated_at_the_shared_entry(session_config, invalid):
    path, config = session_config("debug_intervents.yaml")
    if invalid == "order":
        config["task_order"] = ["intervent_1"]
        message = "exactly two distinct I tasks"
    elif invalid == "role":
        config["segments"]["intervent_1"]["role"] = "key_task"
        message = "only accepts independent intervention tasks"
    elif invalid == "dependency":
        config["segments"]["intervent_2"]["depends_on"] = ["intervent_1"]
        message = "only accepts independent intervention tasks"
    else:
        config["segments"]["intervent_2"]["room_id"] = config["segments"]["intervent_1"]["room_id"]
        message = "distinct room instances"
    path.write_text(yaml.safe_dump(config))

    with pytest.raises(ValueError, match=message):
        Session.load(path)


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


def test_shared_frequency_and_independent_case_seed(session_config):
    path, config = session_config("mb_retrieve_chocolate_from_top_cabinet.yaml")
    config["bridge"]["action_frequency"] = 60
    path.write_text(yaml.safe_dump(config))
    session = Session.load(path)
    assert session.action_frequency == 60
    assert session.case_sampling.get("seed", session.instance_sampling["seed"]) == config["instance_sampling"]["seed"]

    config["case_sampling"] = {"seed": 19}
    path.write_text(yaml.safe_dump(config))
    session = Session.load(path)
    assert session.case_sampling["seed"] == 19
    assert session.instance_sampling["seed"] == config["instance_sampling"]["seed"]


@pytest.mark.parametrize("frequency", [0, -1])
def test_nonpositive_action_frequency_is_rejected(session_config, frequency):
    path, config = session_config("mb_retrieve_chocolate_from_top_cabinet.yaml")
    config["bridge"]["action_frequency"] = frequency
    path.write_text(yaml.safe_dump(config))

    with pytest.raises(ValueError, match="bridge.action_frequency must be positive"):
        Session.load(path)


def test_debug_recordings_use_rawdata_root_and_allow_absolute_paths(session_config):
    path, config = session_config("debug_intervents.yaml")
    session = Session.load(path)
    relative = config["recordings"]["intervent_1"]["hdf5"]
    assert not Path(relative).is_absolute()
    assert session.recording_path("intervent_1") == session.paths.intervent_rawdata_root / relative

    absolute = str(session.paths.intervent_rawdata_root / "custom.hdf5")
    config["recordings"]["intervent_1"]["hdf5"] = absolute
    path.write_text(yaml.safe_dump(config))
    assert Session.load(path).recording_path("intervent_1") == Path(absolute)


def test_debug_recordings_cannot_include_unused_tasks(session_config):
    path, config = session_config("debug_intervents.yaml")
    config["recordings"]["extra_task"] = {"hdf5": "unused.hdf5", "demo": 0}
    path.write_text(yaml.safe_dump(config))

    with pytest.raises(ValueError, match="exactly one recording for each debug task"):
        Session.load(path)


