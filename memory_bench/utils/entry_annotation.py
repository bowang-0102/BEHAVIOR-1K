"""Select an operation entry from an R1Pro recording's pre-action states."""

import math

import numpy as np

from memory_bench.utils.scene_state import get_state_tolerances, scene_objects, state_matches


BASE_LINEAR_SPEED = 0.03
BASE_ANGULAR_SPEED = 0.05
JOINT_SPEED = 0.05
BASE_STABLE_SECONDS = 0.5
EVENT_STABLE_SECONDS = 0.1


def _interaction_state(state):
    return {
        **{key: state[key] for key in ("root_link", "joint_pos") if key in state},
        # Marker counters and derived bounds do not indicate an object operation.
        "non_kin": {
            name: {"value": value["value"]}
            for name, value in state.get("non_kin", {}).items()
            if "value" in value
        },
    }


def find_entry_frame(reader, used_objects):
    """Return (entry, first interaction), retaining the original entry when no stop fits.

    Only the navigation prefix is removed. The source's terminal state is not an
    operation entry, so scan pre-action states only. Bridge synthesis validates
    whether its normal stow / navigate / restore motion can reach the chosen entry.
    """
    frequency = reader.metadata["interface"]["action_frequency"]
    event_frames = max(1, math.ceil(EVENT_STABLE_SECONDS * frequency))
    stable_frames = math.ceil(BASE_STABLE_SECONDS * frequency)
    robot_name = reader.metadata["interface"]["robot_name"]
    tolerance = get_state_tolerances()
    initial = scene_objects(reader.state(0))
    baseline = {name: _interaction_state(initial[name]) for name in used_objects}
    robots = []
    changing_frames = 0
    event_frame = None

    for frame in range(len(reader.group["action"])):
        objects = scene_objects(reader.state(frame))
        robot = objects[robot_name]
        robots.append(robot)
        changed = any(
            not state_matches(_interaction_state(objects[name]), baseline[name], tolerance)
            for name in used_objects
        ) or any(robot.get("ag_obj_constraint_params", {}).values())
        changing_frames = changing_frames + 1 if changed else 0
        if changing_frames == event_frames:
            event_frame = frame - event_frames + 1
            break

    if event_frame is None or event_frame == 0:
        return 0, event_frame

    # R1Pro's first six DOFs are the virtual x/y/z/rx/ry/rz base joints.
    # The fixed root_link velocities alone do not describe the moving base.
    velocities = np.asarray([robot["joint_vel"] for robot in robots[:event_frame]])
    base_stable = (
        (np.linalg.norm(velocities[:, :3], axis=1) <= BASE_LINEAR_SPEED)
        & (np.linalg.norm(velocities[:, 3:6], axis=1) <= BASE_ANGULAR_SPEED)
    )
    moving_frames = np.flatnonzero(~base_stable)
    stop_start = int(moving_frames[-1]) + 1 if len(moving_frames) else 0
    if event_frame - stop_start < stable_frames:
        return 0, event_frame

    for frame in range(stop_start, event_frame):
        if np.all(np.abs(velocities[frame, 6:]) <= JOINT_SPEED) and not any(
            robots[frame].get("ag_obj_constraint_params", {}).values()
        ):
            return frame, event_frame
    return 0, event_frame
