"""A shared template and final TRO preserve the complete checked scene state."""

from copy import deepcopy
from pathlib import Path

import pytest

from memory_bench.utils.instance_scene import build_instance_tro, restore_instance_scene
from memory_bench.utils.scene_state import write_json_atomic


def test_template_and_tro_round_trip_all_state():
    template = {
        "objects_info": {"init_info": {"mug": {}, "robot": {}, "door": {}, "floor": {}}},
        "state": {
            "pos": [0, 0, 0], "ori": [0, 0, 0, 1],
            "registry": {
                "object_registry": {"mug": {"position": 0}, "robot": {"position": 0}, "door": {"open": False}, "floor": {}},
                "system_registry": {},
            },
        },
        "metadata": {"task": {"common": "keep", "obsolete": True}},
        "robot": {"name": "robot"},
    }
    snapshot = deepcopy(template)
    snapshot["state"]["pos"] = [1, 0, 0]
    snapshot["state"]["registry"]["object_registry"]["mug"]["position"] = 2
    snapshot["state"]["registry"]["object_registry"]["door"]["open"] = True
    snapshot["state"]["registry"]["system_registry"] = {"water": {"particles": []}}
    snapshot["metadata"]["task"] = {"common": "keep", "new": None}
    snapshot["robot"]["position"] = [2, 0, 0]
    tro = build_instance_tro(template, snapshot, {"bindings": {"key_1": {"mug.n.04_1": "mug"}}})
    assert "floor" not in tro["state"]["registry"]["object_registry"]
    assert "objects_info" not in tro
    assert tro["task_metadata"] == {"new": None}
    assert restore_instance_scene(template, tro) == snapshot
    assert template["metadata"]["task"]["obsolete"] is True
    assert template["state"]["registry"]["object_registry"]["door"]["open"] is False


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
