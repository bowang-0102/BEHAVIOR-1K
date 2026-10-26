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


@pytest.mark.parametrize("closed_arms", [("left",), ("right",), ("left", "right")])
def test_reset_opens_both_grippers_and_waits_for_them_to_settle(monkeypatch, closed_arms):
    joints = th.tensor([0.1, 0.2, -0.2, 0.05, 0.05, 0.05, 0.05])
    velocities = th.zeros_like(joints)
    gripper_indices = {"left": th.tensor([3, 4]), "right": th.tensor([5, 6])}
    for arm in closed_arms:
        joints[gripper_indices[arm]] = 0.0
    reset = th.tensor([0.1, 0.2, -0.2, 0.05, 0.05, 0.05, 0.05])
    low, high = -th.ones_like(joints), th.ones_like(joints)
    low[3:], high[3:] = 0.0, 0.05
    robot = SimpleNamespace(
        action_dim=8,
        base_action_idx=th.tensor([0, 1, 2]),
        trunk_action_idx=th.tensor([3]),
        trunk_control_idx=th.tensor([0]),
        arm_names=["left", "right"],
        arm_action_idx={"left": th.tensor([4]), "right": th.tensor([5])},
        arm_control_idx={"left": th.tensor([1]), "right": th.tensor([2])},
        gripper_action_idx={"left": th.tensor([6]), "right": th.tensor([7])},
        gripper_control_idx=gripper_indices,
        control_limits={"position": (low, high)},
        reset_joint_pos=reset,
        get_joint_positions=lambda: joints.clone(),
        get_joint_velocities=lambda: velocities.clone(),
        get_linear_velocity=lambda: th.zeros(3),
        get_angular_velocity=lambda: th.zeros(3),
    )
    actions = []
    guards = []
    checks = []

    def step(action):
        before = joints.clone()
        for arm in robot.arm_names:
            target = (action[robot.gripper_action_idx[arm]] + 1) * 0.025
            indices = robot.gripper_control_idx[arm]
            joints[indices] += (target - joints[indices]) * 0.5
        velocities[:] = (joints - before) * 30
        actions.append(action.clone())

    def check_collision(robot):
        checks.append(True)
        return False

    monkeypatch.setitem(
        sys.modules, "omnigibson.utils.motion_planning_utils",
        SimpleNamespace(detect_robot_collision_in_sim=check_collision),
    )
    navigation.move_joints(
        robot, step, robot.reset_joint_pos,
        control_grippers=True, world_guard=lambda: guards.append(True),
    )
    th.testing.assert_close(joints, reset, atol=0.002, rtol=0)
    assert float(velocities.abs().max()) <= 0.05
    th.testing.assert_close(actions[-1][6:], th.ones(2))
    assert all(th.equal(action[:3], th.zeros(3)) for action in actions)
    assert len(actions) == len(guards) == len(checks)


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
        base_action_idx = th.arange(3)
        controllers = {"base": ("base", 0)}

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
        base_scale=np.ones(3),
        motion_grid=None,
        outline=np.asarray([[-1e-6, -1e-6], [1e-6, -1e-6], [1e-6, 1e-6], [-1e-6, 1e-6]]),
    )
    monkeypatch.setattr(navigation, "_yaw", lambda q: 2 * np.arctan2(float(q[2]), float(q[3])))
    monkeypatch.setattr(navigation, "_base_action", lambda robot: th.zeros(3, dtype=th.float64))

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
    monkeypatch.setitem(
        sys.modules, "omnigibson.controllers.controller_view",
        SimpleNamespace(ControllerView=SimpleNamespace(
            preprocess_command=lambda group, command: command * th.as_tensor(state.base_scale),
        )),
    )
    monkeypatch.setattr(
        "memory_bench.utils.navigation_map.forward_navigation_map",
        lambda scene, robot, floor, position_tolerance: (
            state.grid.numpy(),
            (state.grid if state.motion_grid is None else state.motion_grid).numpy(),
            {"outline_xy": state.outline.tolist()},
        ),
    )

    def step(action):
        state.actions.append(tuple(action.tolist()))
        vx, vy, wz = np.asarray(action) * state.base_scale
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
    assert result["steps"] == len(state.actions)
    assert result["duration_seconds"] == len(state.actions) / 30
    np.testing.assert_allclose(state.actions[-1], 0, atol=1e-8)


@pytest.mark.parametrize("target", [[2.8, 1.3], [-2.8, 1.3], [1.3, -2.8]])
@pytest.mark.parametrize("response_fraction", [0.2, 1.0])
def test_open_diagonal_route_avoids_grid_staircase_slowdowns(navigation_scene, target, response_fraction, monkeypatch):
    state = navigation_scene
    state.response_fraction = response_fraction
    # A clear segment should bypass grid search altogether.
    planner = sys.modules["omnigibson.utils.motion_planning_utils"]
    monkeypatch.setattr(planner, "astar", lambda *args, **kwargs: pytest.fail("Unnecessary grid search"))
    result = navigation.drive_to_pose(
        state.env, state.robot,
        {"position": [*target, 0.0], "orientation": state.robot.get_position_orientation()[1]},
        state.step,
    )
    positions = np.asarray(state.positions)[:, :2]
    cruise = (np.linalg.norm(positions, axis=1) > 0.4) & (np.linalg.norm(positions - target, axis=1) > 0.4)
    speeds = np.linalg.norm(state.world_velocities, axis=1)
    np.testing.assert_allclose(speeds[cruise], 0.25, atol=1e-5)
    assert result["route_length"] == pytest.approx(np.linalg.norm(target))
    assert result["position_error"] <= 0.02


@pytest.mark.parametrize("mirror", [False, True])
@pytest.mark.parametrize("response_fraction", [0.2, 1.0])
def test_shortcuts_preserve_obstacle_detour(navigation_scene, mirror, response_fraction):
    state = navigation_scene
    state.response_fraction = response_fraction
    state.grid[100:240, 185:215] = 0
    state.path = np.asarray([[-1.5, -1.0], [-0.5, -1.0], [-0.5, 1.0], [0.5, 1.0], [0.5, -1.0], [1.5, -1.0]])
    if mirror:
        state.grid = th.flip(state.grid, dims=(0,))
        state.path[:, 1] *= -1
    state.robot.position[:2] = state.path[0]
    goal = state.path[-1]
    result = navigation.drive_to_pose(
        state.env, state.robot,
        {"position": [*goal, 0.0], "orientation": state.robot.get_position_orientation()[1]},
        state.step,
    )
    assert np.linalg.norm(goal - state.robot.position[:2]) <= 0.02
    assert np.linalg.norm(goal - state.path[0]) < result["route_length"] < 7.0
    # Independently rasterize every executed interval, including acceleration at turns.
    for start, end in zip([state.path[0], *np.asarray(state.positions)[:-1, :2]], state.positions):
        cells = state.env.scene.trav_map.world_to_map(np.linspace(start[:2], end[:2], 10))
        assert th.all(state.grid[cells[:, 0], cells[:, 1]] != 0)


def test_alignment_corrects_position_drift_and_finishes_with_zero_action(navigation_scene):
    state = navigation_scene
    step = state.step
    disturbed = False

    def drift_during_rotation(action):
        nonlocal disturbed
        step(action)
        if abs(float(action[2])) > 0.05 and not disturbed:
            state.robot.position[0] += 0.04
            disturbed = True

    target_yaw = 1.0
    result = navigation.drive_to_pose(
        state.env, state.robot,
        {"position": [1.0, 0.0, 0.0], "orientation": [0.0, 0.0, np.sin(target_yaw / 2), np.cos(target_yaw / 2)]},
        drift_during_rotation,
    )
    assert disturbed
    assert result["position_error"] <= 0.02 and result["yaw_error"] <= 0.05
    np.testing.assert_allclose(state.actions[-1], 0, atol=1e-8)


def test_alignment_ignores_small_position_jitter(navigation_scene):
    state = navigation_scene
    disturbed = False

    def step(action):
        nonlocal disturbed
        state.step(action)
        if abs(float(action[2])) > 0.05 and not disturbed:
            state.robot.position[0] -= 0.0015
            disturbed = True

    result = navigation.drive_to_pose(
        state.env, state.robot,
        {"position": [1.0, 0.0, 0.0], "orientation": [0.0, 0.0, np.sin(0.5), np.cos(0.5)]},
        step,
    )
    assert disturbed and result["position_error"] <= 0.015
    actions = np.asarray(state.actions)
    rotating = np.abs(actions[:, 2]) > 0.05
    np.testing.assert_allclose(actions[rotating, :2], 0, atol=1e-8)


@pytest.mark.parametrize("drift", [0.015, 0.12])
def test_tracking_margin_can_be_used_without_crossing_the_body_clearance(navigation_scene, drift):
    state = navigation_scene
    state.grid[:200] = 0
    state.motion_grid = th.full_like(state.grid, 255)
    state.motion_grid[:198] = 0
    state.robot.position[:2] = [-1.0, 0.01]
    disturbed = False

    def step(action):
        nonlocal disturbed
        state.step(action)
        if state.robot.position[0] > -0.1 and not disturbed:
            state.robot.position[1] -= drift
            state.positions[-1] = state.robot.position.copy()
            disturbed = True

    def run():
        return navigation.drive_to_pose(
            state.env, state.robot,
            {"position": [1.0, 0.01, 0.0], "orientation": state.robot.get_position_orientation()[1]},
            step,
        )

    if drift > 0.04:
        with pytest.raises(RuntimeError, match="collision-free"):
            run()
    else:
        assert run()["position_error"] <= 0.02
        cells = state.env.scene.trav_map.world_to_map(np.asarray(state.positions)[:, :2])
        assert th.any(state.grid[cells[:, 0], cells[:, 1]] == 0)
        assert th.all(state.motion_grid[cells[:, 0], cells[:, 1]] != 0)
    assert disturbed


def test_navigation_holds_one_stowed_joint_target(navigation_scene, monkeypatch):
    state = navigation_scene
    holds = []
    targets = []

    def hold_action(robot):
        holds.append(True)
        return th.tensor([0.0, 0.0, 0.0, 0.4], dtype=th.float64)

    def step(action):
        targets.append(float(action[3]))
        state.step(action[:3])

    monkeypatch.setattr(navigation, "_base_action", hold_action)
    navigation.drive_to_pose(
        state.env, state.robot,
        {"position": [1.0, 0.0, 0.0], "orientation": state.robot.get_position_orientation()[1]},
        step,
    )
    assert len(holds) == 1 and len(targets) > 1
    np.testing.assert_allclose(targets, 0.4)


@pytest.mark.parametrize("scale", [[0.75, 0.75, 1.0], [0.5, 0.8, 0.6]])
def test_navigation_preserves_teleop_controller_scaling(navigation_scene, scale):
    state = navigation_scene
    state.base_scale = np.asarray(scale)
    target_yaw = 0.8
    result = navigation.drive_to_pose(
        state.env, state.robot,
        {"position": [1.5, 0.7, 0.0], "orientation": [0.0, 0.0, np.sin(target_yaw / 2), np.cos(target_yaw / 2)]},
        state.step,
        linear_speed=0.2,
        angular_speed=0.3,
    )
    assert result["position_error"] <= 0.02 and result["yaw_error"] <= 0.05
    actions = np.asarray(state.actions)
    assert actions[:, 0].max() == pytest.approx(0.2)
    assert np.abs(actions[:, 2]).max() == pytest.approx(0.3)
    assert result["peak_linear_velocity"] == pytest.approx(0.2 * scale[0])
    assert result["peak_angular_velocity"] == pytest.approx(0.3 * scale[2])
    physical = np.asarray(state.actions) * scale
    np.testing.assert_allclose(physical[-1], 0, atol=1e-8)
    dt = 1 / state.env.config["env"]["action_frequency"]
    velocities = np.vstack([np.zeros(2), state.world_velocities])
    assert np.max(np.linalg.norm(np.diff(velocities, axis=0), axis=1)) <= 0.4 * dt + 1e-7
    assert np.max(np.abs(np.diff(np.r_[0, physical[:, 2]]))) <= dt + 1e-7


def test_scaled_controller_braking_uses_physical_deceleration(navigation_scene):
    from memory_bench.utils.navigation_map import braking_motion_clear

    state = navigation_scene
    state.base_scale = np.asarray([0.75, 0.75, 1.0])
    state.robot.yaw = 0.0
    state.robot.velocity[0] = 0.375
    state.motion_grid = state.grid.clone()
    state.motion_grid[:, 211] = 0  # Wall 0.22 m ahead: too close to brake at 0.3 m/s².
    arguments = (state.motion_grid.numpy(), state.env.scene.trav_map, state.outline, [0, 0], 0, 0.375, 0, 1 / 30)
    assert braking_motion_clear(*arguments, 0.4, 1.0)
    assert not braking_motion_clear(*arguments, 0.3, 1.0)
    with pytest.raises(RuntimeError, match="measured velocity"):
        navigation.drive_to_pose(
            state.env, state.robot,
            {"position": [-1.5, 0, 0], "orientation": [0, 0, 0, 1]}, state.step,
            linear_speed=0.5,
        )
    assert not state.actions


@pytest.mark.parametrize("sign", [-1, 1])
def test_alignment_takes_shortest_rotation_across_pi(navigation_scene, sign):
    state = navigation_scene
    state.robot.yaw = sign * (np.pi - 0.08)
    target_yaw = -sign * (np.pi - 0.08)
    result = navigation.drive_to_pose(
        state.env, state.robot,
        {"position": [0.0, 0.0, 0.0], "orientation": [0.0, 0.0, np.sin(target_yaw / 2), np.cos(target_yaw / 2)]},
        state.step,
    )
    assert abs(state.robot.yaw - sign * (np.pi - 0.08)) < 0.2
    assert result["yaw_error"] <= 0.05
    np.testing.assert_allclose(state.actions[-1], 0, atol=1e-8)


@pytest.mark.parametrize("swap_axes", [False, True])
def test_navigation_tracks_cell_centers_beside_an_obstacle(navigation_scene, swap_axes):
    state = navigation_scene
    traversability = state.env.scene.trav_map
    traversability.map_size = 2966
    state.grid = th.full((2966, 2966), 255, dtype=th.uint8)
    # The failed bridge was in this free column beside the coffee table. Its
    # float32 grid corner x=4.2999997 maps back into the blocked neighbor.
    start = np.asarray([4.309376239776611, 5.151571750640869])
    goal = np.asarray([start[0], 5.55])
    state.path = np.asarray([start, [4.31, 5.17], [4.31, 5.53], goal])
    if swap_axes:
        start, goal = start[::-1], goal[::-1]
        state.path = state.path[:, ::-1].copy()
        state.grid[:1698, :] = 0
    else:
        state.grid[:, :1698] = 0
    state.robot.position[:2] = start

    result = navigation.drive_to_pose(
        state.env,
        state.robot,
        {"position": [*goal, 0.0], "orientation": state.robot.get_position_orientation()[1]},
        state.step,
    )

    assert result["position_error"] <= 0.02
    assert state.actions
    # Check both the planned route and the executed motion against occupancy.
    for point in [*result["planned_waypoints"], *state.positions]:
        cell = traversability.world_to_map(th.as_tensor(point[:2]))
        assert state.grid[tuple(cell.tolist())] != 0


@pytest.mark.parametrize("target", [[0.0, 1.0], [-1.0, 0.0], [-0.025, -0.017]])
def test_navigation_turns_to_face_each_travel_direction(navigation_scene, target):
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
    actions = np.asarray(state.actions)
    assert np.any(np.abs(actions[:, 2]) > 0.05)
    assert np.all(actions[:, 0] >= -1e-8)
    np.testing.assert_allclose(actions[:, 1], 0, atol=1e-8)
    moving = actions[:, 0] > 0.01
    bearing = np.arctan2(target[1], target[0])
    errors = (np.asarray(state.yaws)[moving] - bearing + np.pi) % (2 * np.pi) - np.pi
    assert np.max(np.abs(errors)) < 0.1
    assert abs(navigation._angle_difference(state.robot.yaw, 0.03)) <= 0.05


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
    actions = np.asarray(state.actions)
    assert np.all(actions[:, 0] >= -1e-8)
    np.testing.assert_allclose(actions[:, 1], 0, atol=1e-8)


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


def test_navigation_stops_retrying_when_the_robot_is_stuck(navigation_scene):
    state = navigation_scene
    state.response_fraction = 0.0
    with pytest.raises(RuntimeError, match="stopped making progress"):
        navigation.drive_to_pose(
            state.env, state.robot,
            {"position": [2.0, 0.0, 0.0], "orientation": state.robot.get_position_orientation()[1]},
            state.step,
            max_steps_per_waypoint=15,
        )
    assert len(state.actions) == 15


@pytest.mark.parametrize("failure", ["blocked", "unreachable", "out_of_bounds"])
def test_navigation_rejects_invalid_routes_before_moving(navigation_scene, failure):
    state = navigation_scene
    if failure == "blocked":
        state.grid[200, 250] = 0
    elif failure == "unreachable":
        state.path = None
        state.grid[:, 225] = 0
    else:
        state.robot.position[0] = 5.0
    with pytest.raises(RuntimeError, match="clearance|No traversable route|outside"):
        navigation.drive_to_pose(
            state.env,
            state.robot,
            {"position": [1.0, 0.0, 0.0], "orientation": state.robot.get_position_orientation()[1]},
            state.step,
        )
    assert state.actions == []


def test_navigation_checks_the_full_turn_before_moving(navigation_scene):
    from memory_bench.utils.navigation_map import braking_motion_clear, footprint_clear, rotation_clear

    state = navigation_scene
    state.outline = np.asarray([[-0.4, -0.02], [0.4, -0.02], [0.4, 0.02], [-0.4, 0.02]])
    state.grid[214, 214] = 0
    grid = state.grid.numpy()
    traversability = state.env.scene.trav_map
    assert footprint_clear(grid, traversability, state.outline, [0, 0], 0)
    assert footprint_clear(grid, traversability, state.outline, [0, 0], np.pi / 2)
    assert not rotation_clear(grid, traversability, state.outline, [0, 0], 0, np.pi / 2)
    assert not braking_motion_clear(grid, traversability, state.outline, [0, 0], 0.6, 0, 0.8, 1 / 30, 0.4, 1)
    with pytest.raises(RuntimeError, match="clearance"):
        navigation.drive_to_pose(
            state.env, state.robot,
            {"position": [0, 1, 0], "orientation": [0, 0, 0, 1]},
            state.step,
        )
    assert not state.actions


def test_footprint_clearance_keeps_offset_between_robot_base_and_body():
    grid = np.full((21, 21), 255, dtype=np.uint8)
    grid[10, 10] = 0
    eroded, footprint = erode_footprint(grid, [-2, -1], [1, 1], resolution=1)
    # A body extending farther left blocks base positions farther right of the obstacle.
    assert footprint == {"kernel_rows_columns": [3, 4], "anchor_column_row": [2, 1]}
    assert np.all(eroded[9:12, 9:13] == 0)
    assert eroded[10, 8] == 255 and eroded[10, 13] == 255


def test_floor_map_reserves_tracking_margin_separately_from_body_clearance(monkeypatch):
    from memory_bench.utils import navigation_map

    raw = th.full((51, 51), 255, dtype=th.uint8)
    raw[25, 25] = 0
    scene = SimpleNamespace(trav_map=SimpleNamespace(map_resolution=0.02))
    position = th.tensor([0.5, -0.3, 0.0])
    robot = SimpleNamespace(
        get_position_orientation=lambda: (position, th.tensor([0.0, 0.0, 0.0, 1.0])),
        collision_points_world=position + th.tensor([
            [-0.2, -0.1, 0.0], [0.3, -0.1, 0.0], [0.3, 0.15, 1.0], [-0.2, 0.15, 1.0],
        ]),
    )
    monkeypatch.setattr(navigation_map, "current_floor_map", lambda *args: raw)
    monkeypatch.setitem(
        sys.modules, "omnigibson.utils",
        SimpleNamespace(transform_utils=SimpleNamespace(quat2euler=lambda orientation: th.zeros(3))),
    )
    planning, occupancy, footprint = navigation_map.forward_navigation_map(scene, robot, 0, 0.02)
    assert footprint["tracking_margin"] == 0.02
    np.testing.assert_array_equal(occupancy, raw.numpy())
    assert np.all(occupancy[planning != 0] != 0)
    assert np.count_nonzero(planning) < np.count_nonzero(occupancy)
    assert np.any((planning == 0) & (occupancy != 0))


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
