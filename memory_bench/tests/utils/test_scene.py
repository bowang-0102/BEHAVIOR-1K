from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from memory_bench.episodes.generate_bridges import _assert_world_preserved
from memory_bench.tests.helpers import make_scene_snapshot
from memory_bench.utils.scene.instances import (
    build_instance_tro,
    crop_session_scene,
    prepared_scene,
    restore_instance_scene,
)
from memory_bench.utils.scene.state import (
    assert_nonrobot_world_preserved,
    get_state_tolerances,
    remap_robot_name,
    require_objects_match,
    require_robot_match,
    scene_objects,
    state_matches,
    write_json_atomic,
)


def test_session_crop_preserves_dependency_rooms_structures_and_unassigned_objects():
    session = SimpleNamespace(
        scene_model="house_double_floor_lower",
        segments={"key": {"room_id": "kitchen_0", "activity": "turn_on_radio"}},
    )
    object_specs = {
        "counter": ("countertop", ["kitchen_0"]),
        "radio": ("radio", "living_room_0"),
        "rack": ("shelf", ["corridor_0"]),
        "garden_tree": ("tree", ["garden_0"]),
        "garage_tool": ("tool", ["garage_0"]),
        "bath_floor": ("floors", ["bathroom_0"]),
        "bath_wall": ("walls", ["bathroom_0"]),
        "bath_door": ("door", ["bathroom_0"]),
        "unknown": ("decoration", []),
        "untagged": ("decoration", None),
    }
    scene = {
        "objects_info": {"init_info": {
            name: {"args": {"category": category, "in_rooms": rooms}}
            for name, (category, rooms) in object_specs.items()
        }},
        "state": {"registry": {"object_registry": {name: {"value": name} for name in object_specs},
                               "system_registry": {}}},
        "metadata": {"task": {}},
    }
    original = deepcopy(scene)

    cropped = crop_session_scene(session, scene, {"counter"})

    expected = {"counter", "radio", "rack", "bath_wall", "bath_door", "unknown", "untagged"}
    assert set(cropped["objects_info"]["init_info"]) == expected
    assert set(cropped["state"]["registry"]["object_registry"]) == expected
    assert cropped["metadata"]["task"]["memory_bench_rooms"] == ["corridor_0", "kitchen_0", "living_room_0"]
    assert scene == original
    # A required object expands its whole room, including support/ground objects.
    expanded = crop_session_scene(session, scene, {"bath_floor"})
    assert "bath_floor" in expanded["objects_info"]["init_info"]
    assert "bathroom_0" in expanded["metadata"]["task"]["memory_bench_rooms"]
    with pytest.raises(ValueError, match="missing task objects.*missing"):
        crop_session_scene(session, scene, {"missing"})
    session.segments["key"]["activity"] = "chopping_wood"
    expanded = crop_session_scene(session, scene, {"counter"})
    assert "garage_tool" in expanded["objects_info"]["init_info"]
    assert "garage_0" in expanded["metadata"]["task"]["memory_bench_rooms"]


@pytest.mark.parametrize("room_type", ["bedroom", "childs_room"])
def test_single_floor_crop_keeps_corridor_for_room_navigation(room_type):
    session = SimpleNamespace(
        scene_model="house_single_floor",
        segments={"key": {"room_id": f"{room_type}_0", "activity": "test_activity"}},
    )
    scene = make_scene_snapshot({
        "robot": 0, "room_floor": 0, "other_room_floor": 0, "corridor_floor": 0, "garden_floor": 0,
    })
    for name, room in (
        ("room_floor", f"{room_type}_0"), ("other_room_floor", f"{room_type}_1"),
        ("corridor_floor", "corridor_0"), ("garden_floor", "garden_0"),
    ):
        scene["objects_info"]["init_info"][name]["args"].update(category="floors", in_rooms=[room])

    cropped = crop_session_scene(session, scene, set())

    assert "corridor_0" in cropped["metadata"]["task"]["memory_bench_rooms"]
    assert "corridor_floor" in cropped["objects_info"]["init_info"]
    assert f"{room_type}_1" not in cropped["metadata"]["task"]["memory_bench_rooms"]
    assert "other_room_floor" not in cropped["objects_info"]["init_info"]
    assert "garden_floor" not in cropped["objects_info"]["init_info"]


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
    full = make_scene_snapshot({"robot": 0, "mug": 1})
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


def test_state_comparison_tolerates_jitter_and_rejects_displacement():
    expected = {"pos": [0, 0, 0], "lin_vel": [0, 0, 0]}
    actual = {"pos": [0.01, 0, 0], "lin_vel": [0.1, 0, 0]}
    assert state_matches(actual, expected, get_state_tolerances())
    resolved = get_state_tolerances()
    resolved["ignored_fields"].append("joint_vel")
    resolved["numeric"]["pos"] = 1.0
    assert not state_matches({"pos": [0.1, 0, 0]}, {"pos": [0, 0, 0]}, get_state_tolerances())


@pytest.mark.parametrize("state,field", [("HeatSourceOrSink", "heat_source_or_sink"), ("OnFire", "on_fire")])
@pytest.mark.parametrize("active", [False, True])
def test_legacy_state_comparison_accepts_only_inactive_added_flags(state, field, active):
    legacy = make_scene_snapshot({"robot": 0, "toy": 0})
    scene_objects(legacy)["toy"]["non_kin"] = {"Temperature": {"temperature": 23.0}}
    modern = deepcopy(legacy)
    scene_objects(modern)["toy"]["non_kin"][state] = {field: active}
    for actual, expected in ((modern, legacy), (legacy, modern)):
        if active:
            with pytest.raises(ValueError, match="Task handoff.*toy"):
                require_objects_match(actual, expected, {"toy"}, get_state_tolerances(), "Task handoff")
        else:
            require_objects_match(actual, expected, {"toy"}, get_state_tolerances(), "Task handoff")


@pytest.mark.parametrize("array_actual,array_expected", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize(
    "field,actual,expected,matches",
    [
        ("pos", [0.01, 0, 0], [0, 0, 0], True),
        ("pos", [0.1, 0, 0], [0, 0, 0], False),
        ("pos", [[0, 0, 0]], [0, 0, 0], False),
        ("ori", [0, 0, 0, -1], [0, 0, 0, 1], True),
        ("joint_pos", [0.01, 0], [0, 0], True),
        ("particles", [[0, 0, 0], [1, 0, 0]], [[0, 0, 0], [1, 0, 0]], True),
        ("particles", [[0, 0, 0], [2, 0, 0]], [[0, 0, 0], [1, 0, 0]], False),
        ("temperature", 20.0001, 20.0, True),
    ],
)
def test_state_comparison_accepts_live_arrays(array_actual, array_expected, field, actual, expected, matches):
    actual = {field: np.asarray(actual) if array_actual else actual}
    expected = {field: np.asarray(expected) if array_expected else expected}
    assert state_matches(actual, expected, get_state_tolerances()) is matches


@pytest.mark.parametrize("actual_aabb", [None, [0, 0, 0, 1, 1, 1], [2, 2, 2, 3, 3, 3]])
def test_state_comparison_ignores_derived_aabb(actual_aabb):
    before = make_scene_snapshot({"robot": 0, "toy": 0})
    after = deepcopy(before)
    scene_objects(before)["toy"]["non_kin"]["AABB"] = {"aabb": [0, 0, 0, 1, 1, 1]}
    if actual_aabb is not None:
        scene_objects(after)["toy"]["non_kin"]["AABB"] = {"aabb": actual_aabb}
    for actual, expected in ((after, before), (before, after)):
        require_objects_match(actual, expected, {"toy"}, get_state_tolerances(), "Task handoff")
        _assert_world_preserved(expected, actual)


@pytest.mark.parametrize("change", ["position", "joint", "temperature", "toggle", "missing_state", "extra_field"])
def test_task_handoff_rejects_state_differences_in_both_directions(change):
    before = make_scene_snapshot({"robot": 0, "toy": 0})
    obj = scene_objects(before)["toy"]
    obj["joint_pos"] = [0]
    obj["non_kin"] = {"Temperature": {"temperature": 20}, "ToggledOn": {"toggled_on": False}}
    after = deepcopy(before)
    obj = scene_objects(after)["toy"]
    if change == "position":
        obj["root_link"]["pos"][0] = 1
    elif change == "joint":
        obj["joint_pos"][0] = 1
    elif change == "temperature":
        obj["non_kin"]["Temperature"]["temperature"] = 30
    elif change == "toggle":
        obj["non_kin"]["ToggledOn"]["toggled_on"] = True
    elif change == "missing_state":
        del obj["non_kin"]["ToggledOn"]
    else:
        obj["AABB"] = {"aabb": [0, 0, 0, 1, 1, 1]}
    for actual, expected in ((after, before), (before, after)):
        with pytest.raises(ValueError, match="Task handoff.*toy"):
            require_objects_match(actual, expected, {"toy"}, get_state_tolerances(), "Task handoff")


def test_robot_boundary_uses_physical_base_pose():
    entry = make_scene_snapshot({"robot": 0})
    actual = deepcopy(entry)
    # R1Pro's serialized root may remain fixed while virtual base joints move it.
    actual["robot"]["position"] = [2, 0, 0]
    tolerance = {"ignored_fields": [], "numeric": {"default": 0.001, "pos": 0.02, "ori": 0.05}}
    with pytest.raises(ValueError, match="robot pose"):
        require_robot_match(actual, entry, "robot", tolerance, "Endpoint")
    renamed = remap_robot_name(entry, "robot", "r1pro")
    assert renamed["robot"]["name"] == "r1pro"
    assert "r1pro" in scene_objects(renamed) and "robot" not in scene_objects(renamed)


@pytest.mark.parametrize(
    "position,matches", [([0.025, 0, 0], True), ([0.031, 0, 0], False), ([0.025, 0.025, 0], False)]
)
def test_robot_boundary_uses_relaxed_position_radius(position, matches):
    entry = make_scene_snapshot({"robot": 0, "toy": 0})
    actual = deepcopy(entry)
    actual["robot"]["position"] = position
    tolerance = get_state_tolerances()
    if matches:
        require_robot_match(actual, entry, "robot", tolerance, "Endpoint")
    else:
        with pytest.raises(ValueError, match="robot pose"):
            require_robot_match(actual, entry, "robot", tolerance, "Endpoint")
    scene_objects(actual)["toy"]["root_link"]["pos"] = [0.025, 0, 0]
    with pytest.raises(ValueError, match="toy"):
        require_objects_match(actual, entry, {"toy"}, tolerance, "Task handoff")


@pytest.mark.parametrize("change", ["object", "missing_state", "system"])
def test_world_preservation_error_identifies_changed_field(change):
    before = {"registry": {
        "object_registry": {"cup": {
            "root_link": {"pos": [0, 0, 0]},
            "non_kin": {"AABB": {"aabb": [0, 0, 0, 1, 1, 1]}, "Temperature": {"temperature": 20}},
        }},
        "system_registry": {"water": {"n_particles": 0}},
    }}
    after = deepcopy(before)
    del after["registry"]["object_registry"]["cup"]["non_kin"]["AABB"]
    if change == "system":
        after["registry"]["system_registry"]["water"]["n_particles"] = 1
        field = "systems.water.n_particles"
    elif change == "missing_state":
        after["registry"]["object_registry"]["cup"]["non_kin"].clear()
        field = "objects.cup.non_kin.Temperature: missing"
    else:
        after["registry"]["object_registry"]["cup"]["root_link"]["pos"][0] = 0.5
        field = "objects.cup.root_link.pos"

    with pytest.raises(RuntimeError) as error:
        assert_nonrobot_world_preserved(before, after, "robot")

    assert field in str(error.value)
    assert "expected=" in str(error.value)
    if change != "missing_state":
        assert "actual=" in str(error.value)

