from copy import deepcopy
from pathlib import Path

import pytest

from memory_bench.utils.instance_scene import build_instance_tro, restore_instance_scene
from memory_bench.utils.scene_state import write_json_atomic


def test_template_and_tro_round_trip_all_state():
    template = {
        "objects_info": {"init_info": {"mug": {}, "door": {}, "floor": {}}},
        "state": {
            "pos": [0, 0, 0], "ori": [0, 0, 0, 1],
            "registry": {
                "object_registry": {"mug": {"position": 0}, "door": {"open": False}, "floor": {}},
                "system_registry": {},
            },
        },
        "metadata": {"task": {"common": "keep", "obsolete": True}},
    }
    snapshot = deepcopy(template)
    snapshot["state"]["pos"] = [1, 0, 0]
    snapshot["state"]["registry"]["object_registry"]["mug"]["position"] = 2
    snapshot["state"]["registry"]["object_registry"]["door"]["open"] = True
    snapshot["state"]["registry"]["object_registry"]["door"]["joint_pos"] = [1.2]
    snapshot["state"]["registry"]["system_registry"] = {"water": {"particles": []}}
    snapshot["metadata"]["task"] = {"common": "keep", "new": None}
    pose = {"position": [2, 0, 0], "orientation": [0, 0, 0, 1]}
    tro = build_instance_tro(template, snapshot, {"robot": pose})
    assert "floor" not in tro["state"]["registry"]["object_registry"]
    assert "objects_info" not in tro
    assert tro["task_metadata"] == {"new": None}
    assert restore_instance_scene(template, tro) == {**snapshot, "robot": pose}
    assert template["metadata"]["task"]["obsolete"] is True
    assert template["state"]["registry"]["object_registry"]["door"]["open"] is False


def test_unchanged_world_has_no_object_or_metadata_copies():
    from memory_bench.tests.test_pipeline import snapshot
    from memory_bench.utils.instance_scene import prepared_scene

    full = snapshot({"robot": 0, "mug": 1})
    full["init_info"] = {"args": {"scene_file": deepcopy(full)}}
    template = prepared_scene(full, "robot")
    tro = build_instance_tro(template, prepared_scene(full, "robot"), {"robot": full["robot"]})
    assert "scene_file" not in template["init_info"]["args"]
    assert "robot" not in template and "robot" not in template["objects_info"]["init_info"]
    assert "robot" not in template["state"]["registry"]["object_registry"]
    assert tro["state"] == {"registry": {"object_registry": {}}}
    assert "task_metadata" not in tro and "removed_task_metadata" not in tro


def test_atomic_json_failure_does_not_publish_partial_output(tmp_path, monkeypatch):
    target = tmp_path / "instance_0001.json"
    original_replace = Path.replace

    def fail_publish(path, destination):
        if destination == target:
            raise OSError("Interrupted before publish")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", fail_publish)
    with pytest.raises(OSError, match="Interrupted before publish"):
        write_json_atomic(target, {"complete": True})
    assert not target.exists()
    assert not list(tmp_path.iterdir())
