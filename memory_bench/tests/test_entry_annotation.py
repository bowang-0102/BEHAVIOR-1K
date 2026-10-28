import json
from copy import deepcopy

import h5py
import numpy as np
import pytest

from memory_bench.tests.test_pipeline import canonical, snapshot
from memory_bench.utils.entry_annotation import find_entry_frame
from memory_bench.utils.recordings import FrameReader


@pytest.fixture
def recording(tmp_path):
    initial = snapshot({"robot": 0, "toy": 0})
    robot = initial["state"]["registry"]["object_registry"]["robot"]
    robot.update(joint_pos=[0.0] * 28, joint_vel=[0.0] * 28, ag_obj_constraint_params={})
    initial["state"]["registry"]["object_registry"]["toy"]["non_kin"] = {
        "ToggledOn": {"value": False, "hand_in_marker_steps": 0},
    }
    initial["state"]["registry"]["object_registry"]["toy"]["joint_pos"] = [0.0]
    states = []
    for frame in range(121):
        state = deepcopy(initial["state"])
        objects = state["registry"]["object_registry"]
        velocities = objects["robot"]["joint_vel"]
        if 15 <= frame < 45:
            velocities[0] = 0.2
        if 45 <= frame < 60:
            velocities[5] = 0.2
        if frame < 10 or 60 <= frame < 65:
            velocities[10] = 0.2
        # Startup adjustments, a position spike, and quaternion sign changes
        # must not become the first operation.
        objects["toy"]["non_kin"]["ToggledOn"]["hand_in_marker_steps"] = frame
        if frame == 20:
            objects["toy"]["root_link"]["pos"][0] = 0.1
        if 30 <= frame < 40:
            objects["toy"]["root_link"]["ori"] = [0, 0, 0, -1]
        states.append(state)

    def write(event="position"):
        for frame in range(80, len(states)):
            objects = states[frame]["registry"]["object_registry"]
            if event == "position":
                objects["toy"]["root_link"]["pos"][0] = 0.1
            elif event == "joint":
                objects["toy"]["joint_pos"][0] = 0.1
            elif event == "toggle":
                objects["toy"]["non_kin"]["ToggledOn"]["value"] = True
            elif event == "grasp":
                objects["robot"]["ag_obj_constraint_params"] = {"left": {"target_obj": "toy"}}
        source = tmp_path / "operation.hdf5"
        canonical(source, initial, initial, "test")
        with h5py.File(source, "a") as stream:
            group = stream["data/demo_0"]
            del group["state_json"], group["action"]
            group.create_dataset("state_json", data=[json.dumps(state) for state in states], dtype=h5py.string_dtype())
            group.create_dataset("action", data=np.zeros((120, 23), dtype=np.float32))
        return source

    return states, write


@pytest.mark.parametrize("event", ["position", "joint", "toggle", "grasp"])
def test_entry_follows_translation_and_turning_but_preserves_reaching(recording, event):
    _, write = recording
    with FrameReader(write(event)) as reader:
        # Root poses stay fixed, as they do for R1Pro's virtual base.
        assert find_entry_frame(reader, ["toy"]) == (65, 80)


def test_no_object_event_keeps_original_entry(recording):
    _, write = recording
    with FrameReader(write(event=None)) as reader:
        assert find_entry_frame(reader, ["toy"]) == (0, None)


@pytest.mark.parametrize("motion", ["base", "arm"])
def test_no_stable_entry_keeps_original_entry(recording, motion):
    states, write = recording
    for state in states[60:80]:
        state["registry"]["object_registry"]["robot"]["joint_vel"][0 if motion == "base" else 10] = 0.2
    with FrameReader(write()) as reader:
        assert find_entry_frame(reader, ["toy"]) == (0, 80)
