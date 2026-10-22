"""CPU checks for navigation geometry and recorded, closed-loop base actions."""

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


@pytest.fixture
def navigation_scene(monkeypatch):
    """Run the real route tracker against a velocity-controlled CPU robot."""
    class Traversability:
        map_size = 400
        map_resolution = 0.02
        floor_heights = th.tensor([0.0])

        def world_to_map(self, xy):
            return th.flip(th.as_tensor(xy) / self.map_resolution + self.map_size / 2, dims=(-1,)).int()

        def map_to_world(self, cells):
            return th.flip((th.as_tensor(cells) - self.map_size / 2) * self.map_resolution, dims=(-1,))

    class Robot:
        def __init__(self):
            self.position = np.zeros(3)
            self.yaw = 0.03
            self.velocity = np.zeros(3)
            self.angular_velocity = np.zeros(3)

        def get_position_orientation(self):
            return th.tensor(self.position), th.tensor([0.0, 0.0, np.sin(self.yaw / 2), np.cos(self.yaw / 2)])

        def get_linear_velocity(self):
            return th.tensor(self.velocity)

        def get_angular_velocity(self):
            return th.tensor(self.angular_velocity)

    trav_map = Traversability()
    scene = SimpleNamespace(trav_map=trav_map)
    robot = Robot()
    robot.scene = scene
    state = SimpleNamespace(
        env=SimpleNamespace(scene=scene, config={"env": {"action_frequency": 30}}),
        robot=robot,
        grid=th.full((trav_map.map_size, trav_map.map_size), 255, dtype=th.uint8),
        path=np.asarray([[0.0, 0.0], [1.0, 0.0]]),
        actions=[],
        world_velocities=[],
        positions=[],
        yaws=[],
        collision_after=None,
        collision_checks=0,
        response_fraction=1.0,
    )
    monkeypatch.setattr(navigation, "_yaw", lambda q: 2 * np.arctan2(float(q[2]), float(q[3])))
    monkeypatch.setattr(navigation, "_base_action", lambda robot, vx=0, vy=0, wz=0: (vx, vy, wz))

    def astar(grid, source, target, *, allow_corner_cutting):
        assert allow_corner_cutting is False
        if state.path is None:
            return None
        cells = [trav_map.world_to_map(point) for point in state.path]
        return th.stack(cells)

    def collision_check(robot):
        state.collision_checks += 1
        return state.collision_after is not None and len(state.actions) >= state.collision_after

    monkeypatch.setitem(
        sys.modules,
        "omnigibson.utils.motion_planning_utils",
        SimpleNamespace(astar=astar, detect_robot_collision_in_sim=collision_check),
    )
    monkeypatch.setattr(
        "memory_bench.utils.navigation_map.fixed_heading_floor_map",
        lambda scene, robot, floor, position_tolerance: (state.grid, {}),
    )

    def step(action):
        vx, vy, wz = action
        state.actions.append(action)
        c, s = np.cos(robot.yaw), np.sin(robot.yaw)
        commanded_velocity = np.asarray([c * vx - s * vy, s * vx + c * vy, 0.0])
        robot.velocity += state.response_fraction * (commanded_velocity - robot.velocity)
        robot.angular_velocity += state.response_fraction * (np.asarray([0.0, 0.0, wz]) - robot.angular_velocity)
        dt = 1 / state.env.config["env"]["action_frequency"]
        robot.position += robot.velocity * dt
        robot.yaw += robot.angular_velocity[2] * dt
        state.world_velocities.append(commanded_velocity[:2].copy())
        state.positions.append(robot.position.copy())
        state.yaws.append(robot.yaw)

    state.step = step
    return state


def test_straight_route_keeps_speed_across_intermediate_waypoints(navigation_scene):
    state = navigation_scene
    state.path = np.asarray([[0.0, 0.0], [0.5, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]])
    guards = []
    result = navigation.drive_to_pose(
        state.env,
        state.robot,
        {"position": [3.0, 0.0, 0.0], "orientation": state.robot.get_position_orientation()[1]},
        state.step,
        frame_count=lambda: len(state.actions),
        world_guard=lambda: guards.append(True),
    )
    positions = np.asarray(state.positions)
    speeds = np.linalg.norm(state.world_velocities, axis=1)
    cruise = (positions[:, 0] > 0.5) & (positions[:, 0] < 2.5)
    assert np.any(cruise)
    assert np.all(speeds[cruise] > 0.2)
    assert result["position_error"] <= 0.02
    assert result["map_resolution"] == 0.02
    assert result["frames"] == [0, len(state.actions)]
    assert len(guards) == state.collision_checks == len(state.actions)


@pytest.mark.parametrize("target", [[0.0, 1.0], [-1.0, 0.0], [-0.025, -0.017]])
def test_holonomic_translation_keeps_heading(navigation_scene, target):
    # The bridge can snap backwards near a bed; turning toward the grid point
    # sweeps the stowed body into the bed despite a valid sideways translation.
    state = navigation_scene
    state.path = np.asarray([[0.0, 0.0], target])
    navigation.drive_to_pose(
        state.env,
        state.robot,
        {"position": [*target, 0.0], "orientation": state.robot.get_position_orientation()[1]},
        state.step,
        position_tolerance=0.002,
    )
    assert np.linalg.norm(state.robot.position[:2] - target) <= 0.002
    assert state.actions and np.max(np.abs(np.asarray(state.actions)[:, 2])) < 1e-8
    assert abs(state.robot.yaw - 0.03) < 1e-8


def test_corner_tracking_checks_occupancy_and_limits_world_acceleration(navigation_scene):
    state = navigation_scene
    state.env.config["env"]["action_frequency"] = 20
    state.robot.position[:2] = [-1.0, 0.0]
    state.path = np.asarray([[-1.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
    # A blind lookahead across the corner cuts through this obstacle.
    state.grid[208:, :244] = 0
    target_yaw = 0.7
    result = navigation.drive_to_pose(
        state.env,
        state.robot,
        {"position": [1.0, 1.0, 0.0], "orientation": [0.0, 0.0, np.sin(target_yaw / 2), np.cos(target_yaw / 2)]},
        state.step,
        lookahead_distance=0.6,
        linear_speed=0.3,
        linear_acceleration=0.35,
        angular_acceleration=0.8,
    )
    positions = np.asarray(state.positions)
    # Check executed segments independently of the production ray checker.
    for start, end in zip(np.vstack([[-1, 0, 0], positions[:-1]]), positions):
        for position in np.linspace(start, end, 5):
            cell = state.env.scene.trav_map.world_to_map(position[:2])
            assert state.grid[tuple(cell.tolist())] != 0
    velocities = np.vstack([np.zeros(2), state.world_velocities])
    angular_velocities = np.r_[0.0, np.asarray(state.actions)[:, 2]]
    dt = 1 / state.env.config["env"]["action_frequency"]
    assert np.max(np.linalg.norm(np.diff(velocities, axis=0), axis=1)) <= 0.35 * dt + 1e-7
    assert np.max(np.abs(np.diff(angular_velocities))) <= 0.8 * dt + 1e-7
    assert np.max(np.linalg.norm(velocities, axis=1)) <= 0.3 + 1e-7
    near_corner = np.linalg.norm(positions[:, :2] - [1, 0], axis=1) < 0.25
    assert np.any(near_corner)
    assert np.min(np.linalg.norm(np.asarray(state.world_velocities)[near_corner], axis=1)) < 0.25
    assert np.linalg.norm(state.robot.velocity) <= 0.03
    assert np.linalg.norm(state.robot.angular_velocity) <= 0.05
    assert result["position_error"] <= 0.02 and result["yaw_error"] <= 0.05
    still_travelling = np.linalg.norm(positions[:, :2] - [1, 1], axis=1) > 0.1
    np.testing.assert_allclose(np.asarray(state.yaws)[still_travelling], 0.03, atol=1e-7)


@pytest.mark.parametrize("orientation_only", [False, True])
def test_navigation_aligns_and_waits_for_physical_velocity_to_settle(navigation_scene, orientation_only):
    state = navigation_scene
    state.response_fraction = 0.2
    target = [0.0, 0.0] if orientation_only else [1.0, 0.0]
    state.path = np.asarray([[0.0, 0.0], target])
    target_yaw = 0.7
    navigation.drive_to_pose(
        state.env,
        state.robot,
        {"position": [*target, 0.0], "orientation": [0.0, 0.0, np.sin(target_yaw / 2), np.cos(target_yaw / 2)]},
        state.step,
    )
    assert np.linalg.norm(state.robot.position[:2] - target) <= 0.02
    assert abs(state.robot.yaw - target_yaw) <= 0.05
    assert np.linalg.norm(state.robot.velocity) <= 0.03
    assert np.linalg.norm(state.robot.angular_velocity) <= 0.05
    if orientation_only:
        np.testing.assert_allclose(state.positions, 0.0, atol=1e-10)


@pytest.mark.parametrize("failure", ["collision", "world_guard"])
def test_navigation_checks_each_recorded_action_for_failures(navigation_scene, failure):
    state = navigation_scene
    if failure == "collision":
        state.collision_after = 5

    def guard():
        if failure == "world_guard" and len(state.actions) == 5:
            raise RuntimeError("world guard failed")

    with pytest.raises(RuntimeError, match="collision|world guard failed"):
        navigation.drive_to_pose(
            state.env,
            state.robot,
            {"position": [1.0, 0.0, 0.0], "orientation": state.robot.get_position_orientation()[1]},
            state.step,
            world_guard=guard,
        )
    assert len(state.actions) == 5


@pytest.mark.parametrize("failure", ["blocked", "unreachable"])
def test_navigation_rejects_invalid_routes_before_moving(navigation_scene, failure):
    state = navigation_scene
    if failure == "blocked":
        state.grid[200, 250] = 0
    else:
        state.path = None
    with pytest.raises(RuntimeError, match="clearance|No traversable route"):
        navigation.drive_to_pose(
            state.env,
            state.robot,
            {"position": [1.0, 0.0, 0.0], "orientation": state.robot.get_position_orientation()[1]},
            state.step,
        )
    assert state.actions == []


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
