import json
from copy import deepcopy
from pathlib import Path

import numpy as np

ROBOT_POSITION_TOLERANCE = 0.03


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
    mismatches = []
    for name in names:
        if name not in a or name not in b:
            mismatches.append(name)
            continue
        current, recorded = deepcopy(a[name]), deepcopy(b[name])
        # At recording boundaries, omitted legacy flags mean inactive. Motion checks stay strict.
        for state in (current, recorded):
            if "non_kin" in state:
                state["non_kin"].setdefault("HeatSourceOrSink", {"heat_source_or_sink": False})
                state["non_kin"].setdefault("OnFire", {"on_fire": False})
        if not state_matches(current, recorded, tolerances) or not state_matches(recorded, current, tolerances):
            mismatches.append(name)
    if mismatches:
        raise ValueError(f"{context}: incompatible state for {mismatches}")


def require_robot_match(actual, expected, name, tolerances, context):
    a, b = actual["robot"], expected["robot"]
    indices = b["controlled_joint_indices"]
    actual_pose = {
        "ori": a["orientation"],
        "joint_pos": [a["joint_positions"][index] for index in indices],
    }
    expected_pose = {
        "ori": b["orientation"],
        "joint_pos": [b["joint_positions"][index] for index in indices],
    }
    position_matches = np.linalg.norm(np.asarray(a["position"]) - np.asarray(b["position"])) <= ROBOT_POSITION_TOLERANCE
    if (
        a["name"] != name or b["name"] != name or not position_matches
        or not state_matches(actual_pose, expected_pose, tolerances)
    ):
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


def remap_robot_name(snapshot, source_robot, target_robot):
    snapshot = deepcopy(snapshot)
    objects = scene_objects(snapshot)
    if source_robot != target_robot:
        objects[target_robot] = objects.pop(source_robot)
        if "robot" in snapshot:
            snapshot["robot"]["name"] = target_robot
        if "objects_info" in snapshot:
            initializers = snapshot["objects_info"]["init_info"]
            initializers[target_robot] = initializers.pop(source_robot)
            initializers[target_robot]["args"]["name"] = target_robot
            args = initializers[target_robot]["args"]
            if "relative_prim_path" in args:
                args["relative_prim_path"] = f"/{target_robot}"
            bindings = snapshot["metadata"]["task"]["inst_to_name"]
            for key, name in bindings.items():
                if name == source_robot:
                    bindings[key] = target_robot
    return snapshot


def scene_snapshot(scene):
    from omnigibson.utils.config_utils import TorchEncoder

    snapshot = json.loads(json.dumps(scene.save(as_dict=True), cls=TorchEncoder))
    snapshot["robot"] = robot_metadata(scene.robots[0])
    return snapshot


def assert_nonrobot_world_preserved(before, after, robot_name):
    before_objects, after_objects = scene_objects(before), scene_objects(after)
    before_objects = {name: state for name, state in before_objects.items() if name != robot_name}
    after_objects = {name: state for name, state in after_objects.items() if name != robot_name}
    before_systems, after_systems = scene_systems(before), scene_systems(after)
    if set(before_objects) != set(after_objects) or set(before_systems) != set(after_systems):
        raise RuntimeError("Robot transfer changed the nonrobot scene topology")
    tolerance = get_state_tolerances()
    # Allow transient joint-speed jitter during settling while retaining pose checks.
    tolerance["numeric"]["joint_vel"] = 0.1
    changed = [
        name
        for name in before_objects
        if not state_matches(after_objects[name], before_objects[name], tolerance, f"objects.{name}")
    ]
    systems_changed = not state_matches(after_systems, before_systems, tolerance, "systems")
    if not changed and not systems_changed:
        return

    def first_difference(actual, expected, path):
        if isinstance(actual, dict) and isinstance(expected, dict):
            for key, value in expected.items():
                if state_matches(actual, {key: value}, tolerance, path):
                    continue
                field = f"{path}.{key}"
                if key not in actual:
                    return f"{field}: missing (expected={value!r})"
                return first_difference(actual[key], value, field)
        return f"{path}: expected={repr(expected)[:160]}, actual={repr(actual)[:160]}"

    details = [
        first_difference(after_objects[name], before_objects[name], f"objects.{name}")
        for name in changed[:3]
    ]
    if systems_changed:
        details.append(first_difference(after_systems, before_systems, "systems"))
    raise RuntimeError(
        f"Robot transfer changed nonrobot world state ({len(changed)} objects): " + "; ".join(details)
    )
