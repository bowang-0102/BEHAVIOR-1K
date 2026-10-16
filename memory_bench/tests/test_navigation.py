"""CPU checks for preserving route geometry when sampling navigation waypoints."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest
import torch as th

from memory_bench import navigation
from memory_bench.navigation import _sample_waypoints
from memory_bench.utils.navigation_map import erode_footprint, rasterize_mesh_bounds


@pytest.mark.parametrize("moving_part", ["gripper", "base_linear", "base_angular"])
def test_final_joint_target_keeps_recording_until_robot_stops(monkeypatch, moving_part):
    # At the real bridge exit the fingers were already within the position budget
    # while still closing at 0.25 rad/s. All hold actions must remain in the recording.
    steps = []
    guards = []
    robot = SimpleNamespace(
        action_dim=4,
        base_action_idx=th.tensor([0]),
        trunk_action_idx=th.tensor([1]),
        trunk_control_idx=th.tensor([1]),
        arm_names=["left"],
        arm_action_idx={"left": th.tensor([2])},
        arm_control_idx={"left": th.tensor([2])},
        gripper_action_idx={"left": th.tensor([3])},
        gripper_control_idx={"left": th.tensor([3])},
        control_limits={"position": (-th.ones(4), th.ones(4))},
        get_joint_positions=lambda: th.zeros(4),
        get_joint_velocities=lambda: th.tensor([0, 0, 0, 0.25 if len(steps) < 3 else 0]),
        get_linear_velocity=lambda: th.zeros(3),
        get_angular_velocity=lambda: th.zeros(3),
    )
    if moving_part != "gripper":
        robot.get_joint_velocities = lambda: th.zeros(4)
        if moving_part == "base_linear":
            robot.get_linear_velocity = lambda: th.tensor([0.1 if len(steps) < 3 else 0.0, 0.0, 0.0])
        else:
            robot.get_angular_velocity = lambda: th.tensor([0.0, 0.0, 0.1 if len(steps) < 3 else 0.0])
    checks = []

    def collision_check(robot):
        checks.append(True)
        return False

    monkeypatch.setitem(
        sys.modules,
        "omnigibson.utils.motion_planning_utils",
        SimpleNamespace(detect_robot_collision_in_sim=collision_check),
    )
    navigation.move_joints(
        robot,
        lambda action: steps.append(action.clone()),
        th.zeros(4),
        control_grippers=True,
        world_guard=lambda: guards.append(True),
    )
    assert len(steps) == len(guards) == len(checks) == 3
    assert all(th.equal(action, steps[0]) for action in steps)


def test_waypoint_sampling_preserves_l_corner_without_shortcut():
    path = np.asarray([[0, 0], [0.25, 0], [0.5, 0], [0.5, 0.25], [0.5, 0.5]])
    sampled = _sample_waypoints(path)
    np.testing.assert_array_equal(sampled, [[0, 0], [0.5, 0], [0.5, 0.5]])


def test_waypoint_sampling_limits_long_straights_and_retains_endpoints():
    # Coarse supplied points can be subdivided along their existing segments.
    path = np.asarray([[2.0, -1.0], [4.6, -1.0], [4.6, 1.4]])
    sampled = _sample_waypoints(path, max_spacing=1.0)
    np.testing.assert_array_equal(sampled[0], path[0])
    np.testing.assert_array_equal(sampled[-1], path[-1])
    assert np.any(np.all(sampled == path[1], axis=1))
    assert np.all(np.linalg.norm(np.diff(sampled, axis=0), axis=1) <= 1.0)
    assert all(np.isclose(point[1], -1) or np.isclose(point[0], 4.6) for point in sampled)


def test_holonomic_translation_does_not_turn_toward_nearby_grid_point(monkeypatch):
    # The real bridge first snaps 3cm backwards near a bed. Turning toward that
    # point sweeps the stowed robot into the bed despite a valid sideways move.
    class Robot:
        position = np.zeros(3)
        yaw = 0.03

        def get_position_orientation(self):
            return th.tensor(self.position), self.yaw

    robot = Robot()
    actions = []
    monkeypatch.setattr(navigation, "_yaw", float)
    monkeypatch.setattr(navigation, "_base_action", lambda robot, vx=0, vy=0, wz=0: (vx, vy, wz))

    def step(action):
        vx, vy, wz = action
        actions.append(action)
        c, s = np.cos(robot.yaw), np.sin(robot.yaw)
        robot.position[:2] += np.asarray([c * vx - s * vy, s * vx + c * vy]) / 30
        robot.yaw += wz / 30

    navigation._move_base(
        robot,
        step,
        -0.025,
        -0.017,
        robot.yaw,
        linear_speed=0.2,
        angular_speed=0.5,
        max_steps=600,
        position_tolerance=0.002,
        yaw_tolerance=0.05,
    )
    assert np.linalg.norm(robot.position[:2] - [-0.025, -0.017]) <= 0.002
    assert actions and all(abs(action[2]) < 1e-8 for action in actions)
    assert abs(robot.yaw - 0.03) < 1e-8


def test_footprint_clearance_keeps_offset_between_robot_base_and_body():
    grid = np.full((21, 21), 255, dtype=np.uint8)
    grid[10, 10] = 0
    eroded, footprint = erode_footprint(grid, [-2, -1], [1, 1], resolution=1)
    # A body extending farther left blocks base positions farther right of the obstacle.
    assert footprint == {"kernel_rows_columns": [3, 4], "anchor_column_row": [2, 1]}
    assert np.all(eroded[9:12, 9:13] == 0)
    assert eroded[10, 8] == 255 and eroded[10, 13] == 255


def test_individual_mesh_bounds_preserve_hole_that_aggregate_link_bounds_block():
    bounds = np.asarray(
        [
            [[-4, -4, 0.5], [4, -2, 0.6]],
            [[-4, 2, 0.5], [4, 4, 0.6]],
            [[-4, -2, 0.5], [-2, 2, 0.6]],
            [[2, -2, 0.5], [4, 2, 0.6]],
        ]
    )
    grid = np.full((21, 21), 255, dtype=np.uint8)
    for low, high in bounds:
        rasterize_mesh_bounds(grid, low, high, map_resolution=0.5, map_size=21, low_z=0.1, high_z=1.8)
    assert all(grid[row, column] == 0 for row, column in ((10, 16), (10, 4), (16, 10), (4, 10)))
    assert grid[10, 10] == 255
    assert grid[10, 20] == 255
    aggregate = np.full_like(grid, 255)
    rasterize_mesh_bounds(
        aggregate,
        bounds[:, 0].min(axis=0),
        bounds[:, 1].max(axis=0),
        map_resolution=0.5,
        map_size=21,
        low_z=0.1,
        high_z=1.8,
    )
    assert aggregate[10, 10] == 0


def test_mesh_bounds_only_block_colliders_in_robot_height_range():
    bounds = np.asarray([[[-7, -1, -1], [-5, 1, 0]], [[-1, -1, 0.5], [1, 1, 1]], [[5, -1, 3], [7, 1, 4]]], dtype=float)
    grid = np.full((31, 31), 255, dtype=np.uint8)
    for low, high in bounds:
        rasterize_mesh_bounds(grid, low, high, map_resolution=1, map_size=31, low_z=0.1, high_z=1.8)
    assert grid[15, 9] == 255
    assert grid[15, 15] == 0
    assert grid[15, 21] == 255
