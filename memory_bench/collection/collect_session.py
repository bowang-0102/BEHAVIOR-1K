"""Collect one continuous Key/Query recording for a frozen scene instance."""

import argparse
import json
from pathlib import Path

import yaml

from memory_bench.utils.state import read_json, robot_metadata, scene_objects, scene_systems, state_matches, write_json


class SessionCollector:
    """Coordinate task boundaries and physical robot handoffs in an OGRobotServer."""

    def __init__(self, session, instance_dir: Path, recording_path: Path):
        self.session = session
        self.instance_dir = Path(instance_dir)
        self.instance = read_json(self.instance_dir / "instance.json")
        self.scene_file = read_json(self.instance_dir / "scene.json")
        self.recording_path = Path(recording_path).resolve()
        self.state_dir = self.recording_path.with_suffix("")
        self.marks_path = self.state_dir / "marks.yaml"
        self.segment_ids = [*session.key_ids, session.query_id]
        self.case_id = self.instance["case_id"]
        self.server = None
        self.active_segment = None
        self.next_segment_index = 0
        self.advance_requested = False
        self.n_pressed = False
        self.awaiting_ready_generation = None
        self.resume_existing_segment = False
        self.query_open_state = None
        self.object_events = []
        self.segments = {}
        self.transfers = {}
        self.prelude_frames = [0, 0]
        self.invalid_reason = None
        self.interface = None
        self.finished = False

        if self.instance["instance_id"] <= 0:
            raise ValueError("instance_id must be a positive integer")
        if set(self.segment_ids) - set(self.instance["bindings"]):
            raise ValueError("Instance is missing one or more Key/Query BDDL bindings")
        if set(self.segment_ids) - set(self.instance["key_entries"] | {session.query_id: self.instance["query_start"]}):
            raise ValueError("Instance is missing one or more segment entry poses")
        if self.recording_path.exists() or self.state_dir.exists():
            raise FileExistsError(f"Refusing to overwrite an existing recording: {self.recording_path}")

    def restore_initial_scene(self, scene):
        """Install the frozen full-scene state before HDF5 metadata is created."""
        scene.restore(self.scene_file, update_initial_file=True)

    def bind_server(self, server):
        """Attach the live JoyLo server and physically reach the first Key entry."""
        from memory_bench.navigation import drive_to_pose, move_joints

        self.server = server
        robot = server.robot
        if robot.model != "r1pro":
            raise ValueError(f"Session collection requires R1Pro, got {robot.model}")
        wrapper = server.env
        self._write_recording_metadata(wrapper, robot)
        self._record_interface(wrapper, robot)

        first_entry = self._entry_target(self.segment_ids[0])
        start_snapshot = self._scene_snapshot(server.env.scene)

        def world_guard():
            self._assert_nonrobot_world_preserved(start_snapshot, self._scene_snapshot(server.env.scene))

        self._require_empty_hands(robot)
        stow = self._stow_positions(robot)
        move_joints(robot, wrapper.step, stow, world_guard=world_guard)
        self.prelude_frames[0] = len(wrapper.current_traj_history) - 1
        drive_to_pose(
            server.env,
            robot,
            first_entry,
            wrapper.step,
            linear_speed=self._linear_speed,
            angular_speed=self._angular_speed,
            frame_count=lambda: len(wrapper.current_traj_history) - 1,
            position_tolerance=self._pose_tolerances[0],
            yaw_tolerance=self._pose_tolerances[1],
            linear_velocity_tolerance=float(self._robot_boundary.get("linear_velocity", 0.03)),
            angular_velocity_tolerance=float(self._robot_boundary.get("angular_velocity", 0.05)),
            world_guard=world_guard,
        )
        self._move_to_ready(wrapper.step, first_entry, robot, world_guard=world_guard)
        self.prelude_frames[1] = len(wrapper.current_traj_history) - 1
        world_guard()
        self._require_collision_free(robot)
        self._require_empty_hands(robot)
        self._require_entry_pose(robot, first_entry)
        self._require_stable(robot)
        self._notify(f"Ready for {self._label(self.segment_ids[0])}. Press and release N to begin.")

    @property
    def _linear_speed(self):
        return float(self.session.config.get("bridge", {}).get("max_base_linear_velocity", 0.2))

    @property
    def _angular_speed(self):
        return float(self.session.config.get("bridge", {}).get("max_base_angular_velocity", 0.5))

    @property
    def _stability_threshold(self):
        return float(self._robot_boundary.get("joint_velocity", 0.05))

    @property
    def _robot_boundary(self):
        return self.session.config.get("validation", {}).get("robot_boundary", {})

    @property
    def _pose_tolerances(self):
        numeric = self.session.config["validation"]["state_tolerances"]["numeric"]
        return (
            float(numeric["pos"]),
            float(self._robot_boundary.get("yaw_tolerance", numeric["ori"])),
        )

    @property
    def _joint_tolerance(self):
        numeric = self.session.config["validation"]["state_tolerances"]["numeric"]
        return float(numeric["joint_pos"])

    @property
    def waiting_for_ready(self):
        return self.awaiting_ready_generation is not None

    def keyboard_event(self, event):
        """Accept one N action only after a full press/release cycle."""
        from omnigibson.lazy import carb

        if event.input != carb.input.KeyboardInput.N:
            return
        if event.type == carb.input.KeyboardEventType.KEY_PRESS:
            self.n_pressed = True
        elif event.type == carb.input.KeyboardEventType.KEY_RELEASE and self.n_pressed:
            self.n_pressed = False
            self.advance_requested = True

    def process(self):
        """Advance one boundary or wait for the teleop client to resynchronize."""
        if self.finished:
            return
        server = self.server
        if self.advance_requested:
            self.advance_requested = False
            if server._waiting_to_resume:
                self._begin_ready_handshake(resume_existing=self.active_segment is not None)
            elif self.active_segment is not None:
                self._finish_active_segment()

        if self.awaiting_ready_generation is not None:
            generation, command = server.session_command_snapshot()
            if generation > self.awaiting_ready_generation and self._ready_command_is_synced(command):
                self.awaiting_ready_generation = None
                if not self.resume_existing_segment:
                    self._start_segment()
                self.resume_existing_segment = False

    def after_step(self):
        """Called after each operator action to retain every Query object event."""
        if self.active_segment == self.session.query_id:
            self._collect_query_events()

    def abort(self, reason="operator stopped collection"):
        self.invalid_reason = self.invalid_reason or reason
        self._write_marks()
        self.finished = True

    def _write_recording_metadata(self, wrapper, robot):
        data = wrapper.hdf5_file["data"]
        config = wrapper.env.env_config
        data.attrs["fps"] = wrapper.fps
        data.attrs["action_frequency"] = config["action_frequency"]
        data.attrs["control_frequency"] = config["action_frequency"]
        data.attrs["robot_model"] = robot.model
        data.attrs["action_dim"] = robot.action_dim
        data.attrs["session_id"] = self.session.session_id
        data.attrs["instance_id"] = self.instance["instance_id"]
        data.attrs["case_id"] = self.case_id
        data.attrs["action_schema"] = json.dumps(
            {
                "base": "robot_frame_velocity_xyz_yaw",
                "trunk": "joint_position_target",
                "arms": "joint_position_target",
                "grippers": "JoyLo grasp command",
            }
        )

    def _record_interface(self, wrapper, robot):
        config = json.loads(wrapper.hdf5_file["data"].attrs["config"])
        robot_config = config["robots"][0]
        robot_type = (robot_config["model"] if "model" in robot_config else robot_config["type"]).lower()
        self.interface = {
            "robot_type": robot_type,
            "robot_name": robot_config["name"],
            "controller_config": robot_config["controller_config"],
            "action_normalize": robot_config["action_normalize"],
            "action_dim": robot.action_dim,
            "action_frequency": config["env"]["action_frequency"],
            "fps": config["env"]["rendering_frequency"],
            "physics_frequency": config["env"]["physics_frequency"],
        }
        if robot_type != "r1pro" or self.interface["action_normalize"]:
            raise ValueError("Collection requires the JoyLo R1Pro interface with action_normalize=false")
        if self.interface["action_frequency"] != self.interface["fps"]:
            raise ValueError("Collection requires one action per rendered frame")

    def _begin_ready_handshake(self, resume_existing=False):
        self.server.resume_control()
        self.awaiting_ready_generation = self.server.session_command_snapshot()[0]
        self.resume_existing_segment = resume_existing
        self._notify("Waiting for GELLO to sync to the R1Pro. Keep clear until the robot is ready.")

    def _ready_command_is_synced(self, command):
        import torch as th

        robot = self.server.robot
        joint_positions = robot.get_joint_positions()
        joint_tolerance = self._joint_tolerance
        if float(th.max(th.abs(command["base"]))) > 1e-4:
            return False
        if float(th.max(th.abs(command["trunk"]))) > 1e-4:
            return False

        generated_targets = self.server.session_neutral_targets(command)
        trunk_error = float(th.max(th.abs(generated_targets["trunk"] - joint_positions[robot.trunk_control_idx])))
        if trunk_error > joint_tolerance:
            return False
        for arm in robot.arm_names:
            arm_error = float(th.max(th.abs(generated_targets[arm] - joint_positions[robot.arm_control_idx[arm]])))
            if arm_error > joint_tolerance:
                return False
            if abs(float(command[f"{arm}_gripper"][0]) - self.server._grasp_action[arm]) > 1e-6:
                return False
            gripper_error = float(
                th.max(th.abs(generated_targets[f"{arm}_gripper"] - joint_positions[robot.gripper_control_idx[arm]]))
            )
            if gripper_error > joint_tolerance:
                return False
        return all(obj is None for obj in robot._ag_obj_in_hand.values())

    def _start_segment(self):
        segment_id = self.segment_ids[self.next_segment_index]
        robot = self.server.robot
        target = self._entry_target(segment_id)
        self._validate_segment_entry(segment_id)
        self._require_empty_hands(robot)
        self._require_collision_free(robot)
        self._require_stable(robot)
        self._require_entry_pose(robot, target)
        entry_path = self._state_path(segment_id, "entry")
        write_json(entry_path, self._scene_snapshot(self.server.env.scene))
        frame = len(self.server.env.current_traj_history) - 1
        self.segments[segment_id] = {
            "frames": [frame, None],
            "entry_state": entry_path.name,
            "exit_state": None,
            "validation": {"entry": True, "empty_hands": True, "collision_free": True},
        }
        self.active_segment = segment_id
        if segment_id == self.session.query_id:
            objects = self._live_bindings(self.session.query_id)
            _, self.query_open_state = self.session.task.query_events(objects, case_id=self.case_id)
        self._notify(f"Recording {self._label(segment_id)}: {self._instruction(segment_id)}")

    def _finish_active_segment(self):
        segment_id = self.active_segment
        robot = self.server.robot
        self._require_empty_hands(robot)
        self._require_collision_free(robot)
        stable = self._is_stable(robot)
        if not stable:
            self._notify(
                f"{self._label(segment_id)} is not at rest. Keep the robot still, then press and release N again."
            )
            return
        objects = self._live_bindings(segment_id)
        passed = self.session.task.check_segment(
            segment_id,
            self.session.segments[segment_id]["role"],
            objects,
            case_id=self.case_id,
        )
        if not passed:
            self._notify(f"{self._label(segment_id)} postcondition failed; continue teleoperation and retry N.")
            return
        if segment_id == self.session.query_id and self.invalid_reason is not None:
            self._notify(f"Query contains a forbidden object event: {self.invalid_reason}. This recording is invalid.")
            return

        exit_path = self._state_path(segment_id, "exit")
        end = len(self.server.env.current_traj_history) - 1
        exit_snapshot = self._scene_snapshot(self.server.env.scene)
        write_json(exit_path, exit_snapshot)
        mark = self.segments[segment_id]
        mark["frames"][1] = end
        mark["exit_state"] = exit_path.name
        mark["validation"].update(exit=True, empty_hands=True, stable=True, collision_free=True)
        self.next_segment_index += 1

        if self.next_segment_index == len(self.segment_ids):
            self._write_marks()
            self.finished = True
            self.server._session_stop_requested = True
            self._notify("Query passed. Session recording is complete.")
            return

        next_segment = self.segment_ids[self.next_segment_index]
        self.server.pause()
        self._navigate_to_next(segment_id, next_segment, exit_snapshot)
        self.active_segment = None
        self._notify(f"Ready for {self._label(next_segment)}. Press and release N to begin.")

    def _navigate_to_next(self, previous_segment, next_segment, previous_exit):
        from memory_bench.navigation import drive_to_pose, move_joints

        wrapper = self.server.env
        robot = self.server.robot
        nav_start = len(wrapper.current_traj_history) - 1

        def world_guard():
            self._assert_nonrobot_world_preserved(previous_exit, self._scene_snapshot(wrapper.scene))

        self._require_empty_hands(robot)
        stow = self._stow_positions(robot)
        move_joints(robot, wrapper.step, stow, world_guard=world_guard)
        target = self._entry_target(next_segment)
        drive_to_pose(
            wrapper,
            robot,
            target,
            wrapper.step,
            linear_speed=self._linear_speed,
            angular_speed=self._angular_speed,
            frame_count=lambda: len(wrapper.current_traj_history) - 1,
            position_tolerance=self._pose_tolerances[0],
            yaw_tolerance=self._pose_tolerances[1],
            linear_velocity_tolerance=float(self._robot_boundary.get("linear_velocity", 0.03)),
            angular_velocity_tolerance=float(self._robot_boundary.get("angular_velocity", 0.05)),
            world_guard=world_guard,
        )
        self._move_to_ready(wrapper.step, target, robot, world_guard=world_guard)
        current_snapshot = self._scene_snapshot(wrapper.scene)
        self._assert_nonrobot_world_preserved(previous_exit, current_snapshot)
        self._require_collision_free(robot)
        self._require_empty_hands(robot)
        self._require_entry_pose(robot, target)
        self._require_stable(robot)
        self.transfers[f"transfer_{len(self.transfers)}"] = {
            "from_segment": previous_segment,
            "to_segment": next_segment,
            "navigation_frames": [nav_start, len(wrapper.current_traj_history) - 1],
        }
        self._write_marks()

    def _collect_query_events(self):
        objects = self._live_bindings(self.session.query_id)
        events, current_open = self.session.task.query_events(
            objects,
            previous_open=self.query_open_state,
            case_id=self.case_id,
        )
        frame = len(self.server.env.current_traj_history) - 2
        for event in events:
            forbidden = event["type"] == "wrong_cabinet_opened"
            self.object_events.append({**event, "frame": frame, "forbidden": forbidden})
            if forbidden:
                self.invalid_reason = f"wrong cabinet opened: {event['bddl_instance']}"
                self._notify(f"Forbidden Query event: {self.invalid_reason}.")
        self.query_open_state = current_open

    def _live_bindings(self, segment_id):
        return {
            instance: self.server.env.scene.object_registry("name", object_name)
            for instance, object_name in self.instance["bindings"][segment_id].items()
        }

    def _entry_target(self, segment_id):
        return (
            self.instance["query_start"]
            if segment_id == self.session.query_id
            else self.instance["key_entries"][segment_id]
        )

    def _ready_positions(self, entry, robot):
        return entry["joint_positions"] if "joint_positions" in entry else robot.reset_joint_pos

    def _move_to_ready(self, step, entry, robot, world_guard=None):
        from memory_bench.navigation import move_joints

        move_joints(
            robot,
            step,
            self._ready_positions(entry, robot),
            joint_tolerance=self._joint_tolerance,
            control_grippers="joint_positions" in entry,
            world_guard=world_guard,
        )
        self.server.sync_session_control_state()

    def _stow_positions(self, robot):
        bridge = self.session.config.get("bridge", {})
        return bridge["stow_joint_positions"] if "stow_joint_positions" in bridge else robot.reset_joint_pos

    def _require_entry_pose(self, robot, target):
        from memory_bench.navigation import _angle_difference, _orientation_error, _yaw

        position, orientation = robot.get_position_orientation()
        import torch as th

        position = th.as_tensor(position).cpu().numpy()
        target_position = th.as_tensor(target["position"]).cpu().numpy()
        position_error = float(((position[:3] - target_position[:3]) ** 2).sum() ** 0.5)
        yaw_error = abs(_angle_difference(_yaw(target["orientation"]), _yaw(orientation)))
        orientation_error = _orientation_error(target["orientation"], orientation)
        position_tolerance, yaw_tolerance = self._pose_tolerances
        if position_error > position_tolerance or yaw_error > yaw_tolerance or orientation_error > yaw_tolerance:
            raise RuntimeError(
                f"Robot is outside the entry target tolerance: position={position_error:.3f}m, "
                f"yaw={yaw_error:.3f}rad, orientation={orientation_error:.3f}"
            )
        actual_joint_positions = robot.get_joint_positions()
        target_joint_positions = th.as_tensor(
            self._ready_positions(target, robot),
            dtype=actual_joint_positions.dtype,
            device=actual_joint_positions.device,
        ).clone()
        controlled_indices = robot_metadata(robot)["controlled_joint_indices"]
        if "joint_positions" not in target:
            for arm in robot.arm_names:
                indices = robot.gripper_control_idx[arm]
                target_joint_positions[indices] = actual_joint_positions[indices]
        joint_error = float(
            th.max(th.abs(actual_joint_positions[controlled_indices] - target_joint_positions[controlled_indices]))
        )
        if joint_error > self._joint_tolerance:
            raise RuntimeError(f"Robot is outside the entry joint target tolerance: error={joint_error:.3f}rad")

    def _require_empty_hands(self, robot):
        if any(obj is not None for obj in robot._ag_obj_in_hand.values()):
            raise RuntimeError("Every Key and Query boundary requires empty hands")

    def _is_stable(self, robot):
        import torch as th

        joint_indices = th.cat(
            [
                robot.trunk_control_idx,
                *[robot.arm_control_idx[arm] for arm in robot.arm_names],
                *[robot.gripper_control_idx[arm] for arm in robot.arm_names],
            ]
        )
        joint_speed = float(th.max(th.abs(robot.get_joint_velocities()[joint_indices])))
        linear_speed = float(robot.get_linear_velocity().norm())
        angular_speed = float(robot.get_angular_velocity().norm())
        return (
            joint_speed <= self._stability_threshold
            and linear_speed <= float(self._robot_boundary.get("linear_velocity", 0.03))
            and angular_speed <= float(self._robot_boundary.get("angular_velocity", 0.05))
        )

    def _require_stable(self, robot):
        if not self._is_stable(robot):
            raise RuntimeError("Robot is moving too quickly to mark a stable task boundary")

    def _require_collision_free(self, robot):
        from omnigibson.utils.motion_planning_utils import detect_robot_collision_in_sim

        if detect_robot_collision_in_sim(robot):
            raise RuntimeError("Robot collision detected at a collection boundary")

    def _scene_snapshot(self, scene):
        from omnigibson.utils.config_utils import TorchEncoder

        snapshot = json.loads(json.dumps(scene.save(as_dict=True), cls=TorchEncoder))
        if len(scene.robots) != 1:
            raise RuntimeError(f"Session boundaries require one R1Pro, found {len(scene.robots)} robots")
        snapshot["robot"] = robot_metadata(scene.robots[0])
        return snapshot

    def _assert_nonrobot_world_preserved(self, before, after):
        before_objects, after_objects = scene_objects(before), scene_objects(after)
        robot_name = self.server.robot.name
        before_objects = {name: state for name, state in before_objects.items() if name != robot_name}
        after_objects = {name: state for name, state in after_objects.items() if name != robot_name}
        before_systems, after_systems = scene_systems(before), scene_systems(after)
        if set(before_objects) != set(after_objects) or set(before_systems) != set(after_systems):
            raise RuntimeError("Robot transfer changed the nonrobot scene topology")
        config = self.session.config["validation"]["state_tolerances"]
        tolerance = {"ignored_fields": config["ignored_fields"], "numeric": config["numeric"]}
        changed = [
            name
            for name in before_objects
            if not state_matches(after_objects[name], before_objects[name], tolerance, f"objects.{name}")
        ]
        if changed or not state_matches(after_systems, before_systems, tolerance, "systems"):
            raise RuntimeError(f"Robot transfer changed nonrobot world state: {changed or ['particle systems']}")

    def _state_path(self, segment_id, boundary):
        return self.state_dir / f"{segment_id}_{boundary}.json"

    def _validate_segment_entry(self, segment_id):
        segment_index = self.segment_ids.index(segment_id)
        if segment_index:
            previous_id = self.segment_ids[segment_index - 1]
            previous_objects = self._live_bindings(previous_id)
            if not self.session.task.check_segment(
                previous_id,
                self.session.segments[previous_id]["role"],
                previous_objects,
                case_id=self.case_id,
            ):
                raise RuntimeError(f"Previous segment {previous_id} postcondition does not hold at this entry")

        objects = self._live_bindings(segment_id)
        role = self.session.segments[segment_id]["role"]
        if not self.session.task.check_entry(segment_id, objects, case_id=self.case_id):
            raise RuntimeError(f"{self._label(segment_id)} live entry predicates do not hold")
        if segment_id == self.session.query_id:
            from omnigibson.object_states import Open

            for cabinet in self.session.task.query_cabinets:
                if objects[cabinet].states[Open].get_value():
                    raise RuntimeError(f"Query must begin with every target cabinet closed: {cabinet} is open")
        if self.session.task.check_segment(segment_id, role, objects, case_id=self.case_id):
            raise RuntimeError(f"{self._label(segment_id)} postcondition is already satisfied at entry")

    def _instruction(self, segment_id):
        activity = self.session.segments[segment_id]["activity"]
        return self.session.task.instruction_for(activity, self.case_id)

    def _label(self, segment_id):
        return "Query" if segment_id == self.session.query_id else segment_id.replace("_", " ").title()

    def _notify(self, message):
        print(f"[Memory Bench] {message}")
        if self.server is not None:
            from joylo.gello.utils import og_teleop_utils as utils

            utils.add_status_event(self.server.event_queue, "session", message)

    def _write_marks(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        hdf5_path = self.session.paths.relative(self.recording_path)
        marks = {
            "recording": {
                "hdf5": hdf5_path,
                "demo": 0,
                "instance_id": self.instance["instance_id"],
                "case_id": self.case_id,
                "state_dir": self.session.paths.relative(self.state_dir),
                "prelude_frames": self.prelude_frames,
                "interface": self.interface,
            },
            "segments": self.segments,
            "transfers": self.transfers,
            "object_events": self.object_events,
            "valid": self.invalid_reason is None,
        }
        if self.invalid_reason:
            marks["invalid_reason"] = self.invalid_reason
        temp_path = self.marks_path.with_suffix(".yaml.tmp")
        temp_path.write_text(yaml.safe_dump(marks, sort_keys=False))
        temp_path.replace(self.marks_path)


def collect_session(
    session,
    instance_id: int,
    recording_path: Path | None = None,
    *,
    host="127.0.0.1",
    port=6001,
):
    """Build and run a JoyLo server for one prepared session instance."""
    if instance_id < 1 or instance_id > session.config["instance_sampling"]["count"]:
        raise ValueError(f"instance_id must be in 1..{session.config['instance_sampling']['count']}")
    instance_dir = session.paths.resolve(f"instances/{session.session_id}/instance_{instance_id:04d}")
    if recording_path is None:
        recording_path = session.paths.resolve(
            f"recordings/{session.session_id}/instance_{instance_id:04d}_recording_0001.hdf5"
        )
    Path(recording_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    collector = SessionCollector(session, instance_dir, recording_path)

    from joylo.gello.utils.og_teleop_utils import generate_basic_environment_config

    action_frequency = int(session.config["collection"].get("action_frequency", 30))
    config = generate_basic_environment_config("r1pro", "robot")
    config["env"].update(
        action_frequency=action_frequency,
        rendering_frequency=action_frequency,
        physics_frequency=action_frequency * 4,
        automatic_reset=False,
        flatten_action_space=False,
        flatten_obs_space=False,
    )
    config["scene"] = {
        "type": "InteractiveTraversableScene",
        "scene_model": session.task.scene_model,
        "scene_file": str(collector.instance_dir / "scene.json"),
        "load_room_types": None,
        "load_room_instances": None,
        "include_robots": False,
        "trav_map_with_objects": False,
    }
    config["task"] = {"type": "DummyTask"}

    from joylo.gello.robots.og_robot import OGRobotServer

    server = OGRobotServer(
        robot="r1pro",
        robot_name="robot",
        host=host,
        port=port,
        config=config,
        recording_path=str(collector.recording_path),
        task_name=None,
        partial_load=False,
        instance_id=None,
        ghosting=True,
        session_collector=collector,
    )
    server.serve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Session YAML path")
    parser.add_argument("instance_id", type=int)
    parser.add_argument("--recording", type=Path)
    args = parser.parse_args()

    from memory_bench.session import Session

    session = Session.load(args.config)
    session.paths.activate()
    collect_session(session, args.instance_id, args.recording)


if __name__ == "__main__":
    main()
