import json
from copy import deepcopy
from pathlib import Path

import numpy as np


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, default=lambda value: value.tolist())


def write_json_atomic(path, value):
    """Publish a complete preparation result so an interruption cannot leave a partial JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, default=lambda value: value.tolist()))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def scene_objects(snapshot):
    state = snapshot["state"] if "state" in snapshot else snapshot
    registry = state["registry"] if "registry" in state else state
    return registry["object_registry"]


def scene_systems(snapshot):
    state = snapshot["state"] if "state" in snapshot else snapshot
    registry = state["registry"] if "registry" in state else state
    return registry["system_registry"]


def merge_scene_assets(base, source, names):
    """Copy explicitly allowed independent assets without replacing the world."""
    result = deepcopy(base)
    initializers = result["objects_info"]["init_info"]
    objects = scene_objects(result)
    for name in names:
        incoming = source["objects_info"]["init_info"][name]
        if name in initializers:
            a, b = initializers[name]["args"], incoming["args"]
            for field in ("category", "model", "scale", "fixed_base"):
                if a.get(field) != b.get(field):
                    raise ValueError(f"Conflicting asset {name}: {field}")
        initializers[name] = deepcopy(incoming)
        objects[name] = deepcopy(scene_objects(source)[name])
    return result


def get_state_tolerances():
    """State-comparison thresholds; still require physical calibration."""
    return {
        "ignored_fields": ["is_asleep", "lin_vel", "ang_vel"],
        "numeric": {
            "default": 0.001,
            "pos": 0.02,
            "aabb": 0.02,  # Derived bounds use the same spatial budget as object position.
            "ori": 0.05,
            "joint_pos": 0.02,
            "joint_vel": 0.02,
        },
    }


def state_matches(actual, expected, tolerances, path=""):
    """Compare expected state leaves, ignoring transient fields and derived AABBs."""
    leaf = path.rsplit(".", 1)[-1]
    if leaf in tolerances["ignored_fields"]:
        return True
    if isinstance(expected, dict):
        # AABB is recomputed from pose; legacy recordings omit this derived cache.
        return isinstance(actual, dict) and all(
            key in actual and state_matches(actual[key], value, tolerances, f"{path}.{key}")
            for key, value in expected.items()
            if key not in tolerances["ignored_fields"] and not (leaf == "non_kin" and key == "AABB")
        )
    if isinstance(expected, (list, int, float, np.ndarray)) and not isinstance(expected, bool):
        actual, expected = np.asarray(actual), np.asarray(expected)
        if actual.shape != expected.shape:
            return False
        if leaf == "ori" and np.dot(actual, expected) < 0:
            actual = -actual
        tolerance = tolerances["numeric"][leaf] if leaf in tolerances["numeric"] else tolerances["numeric"]["default"]
        return bool(np.allclose(actual, expected, atol=tolerance, rtol=0))
    return actual == expected


def require_objects_match(actual, expected, names, tolerances, context):
    a, b = scene_objects(actual), scene_objects(expected)
    mismatches = [
        name for name in names
        if name not in a or name not in b
        or not state_matches(a[name], b[name], tolerances)
        or not state_matches(b[name], a[name], tolerances)
    ]
    if mismatches:
        raise ValueError(f"{context}: incompatible state for {mismatches}")


def require_robot_match(actual, expected, name, tolerances, context):
    a, b = actual["robot"], expected["robot"]
    indices = b["controlled_joint_indices"]
    actual_pose = {
        "pos": a["position"],
        "ori": a["orientation"],
        "joint_pos": [a["joint_positions"][index] for index in indices],
    }
    expected_pose = {
        "pos": b["position"],
        "ori": b["orientation"],
        "joint_pos": [b["joint_positions"][index] for index in indices],
    }
    if a["name"] != name or b["name"] != name or not state_matches(actual_pose, expected_pose, tolerances):
        raise ValueError(f"{context}: incompatible robot pose or joint positions")


def robot_metadata(robot):
    """Physical base pose and controlled joints, independent of holonomic root anchors."""
    position, orientation = robot.get_position_orientation()
    indices = [*robot.trunk_control_idx.tolist()]
    for arm in robot.arm_names:
        indices.extend(robot.arm_control_idx[arm].tolist())
        indices.extend(robot.gripper_control_idx[arm].tolist())
    return {
        "name": robot.name,
        "position": position.tolist(),
        "orientation": orientation.tolist(),
        "joint_positions": robot.get_joint_positions().tolist(),
        "controlled_joint_indices": sorted(set(indices)),
    }
