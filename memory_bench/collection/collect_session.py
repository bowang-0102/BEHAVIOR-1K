"""Collect all Keys followed by Query in one continuous JoyLo recording.

SessionCollector owns segment sequencing, task checks and recording marks.
teleop_utils owns controller synchronization, robot restoration and physical checks.
"""

import argparse
import json
from pathlib import Path

import yaml

from memory_bench.collection import teleop_utils
from memory_bench.utils.instance_scene import load_instance_scene
from memory_bench.utils.scene_state import write_json


class SessionCollector:
    """Advance a prepared instance through Key(s) -> Query without resetting the world.

    JoyLo calls keyboard_event() for input, process() before each simulation step,
    and after_step() after each operator action. A segment starts only after N and
    a fresh, synchronized GELLO command; another N requests its completion.
    """

    def __init__(self, session, instance_path: Path, recording_path: Path):
        self.session = session
        self.instance_path = Path(instance_path)
        self.instance, self.scene_file = load_instance_scene(session, self.instance_path)
        self.recording_path = Path(recording_path).resolve()
        self.state_dir = self.recording_path.with_suffix("")
        self.marks_path = self.state_dir / "marks.yaml"
        self.segment_ids = [*session.key_ids, session.query_id]
        self.case_id = self.instance["case_id"]
        self.server = None

        # Control flow: no active segment means we are between tasks. While
        # waiting for a fresh GELLO command, JoyLo holds simulation stepping.
        self.active_segment = None
        self.next_segment_index = 0
        self.advance_requested = False
        self.n_pressed = False
        self.awaiting_ready_generation = None
        # Remember what N requested until the asynchronous handshake completes.
        self.resume_existing_segment = False
        self.finished = False

        # Recording annotations consumed by the later K/Q splitting stage.
        self.query_open_state = None
        self.object_events = []
        self.segments = {}
        self.transfers = {}
        self.prelude_frames = [0, 0]
        self.invalid_reason = None
        self.interface = None

        if self.instance["instance_id"] <= 0:
            raise ValueError("instance_id must be a positive integer")
        if set(self.segment_ids) - set(self.instance["bindings"]):
            raise ValueError("Instance is missing one or more Key/Query BDDL bindings")
        if not self.instance["validation"].get("shared_initial_robot_state"):
            raise ValueError("Reprepare instances with a shared first-Key robot state before collection")
        if self.recording_path.exists() or self.state_dir.exists():
            raise FileExistsError(f"Refusing to overwrite an existing recording: {self.recording_path}")

    # JoyLo lifecycle callbacks

    def restore_initial_scene(self, scene):
        """Install the frozen full-scene state before HDF5 metadata is created."""
        scene.restore(self.scene_file, update_initial_file=True)

    def bind_server(self, server):
        """Attach JoyLo and restore the instance's common Key/Query robot start."""
        self.server = server
        robot = server.robot
        if robot.model != "r1pro":
            raise ValueError(f"Session collection requires R1Pro, got {robot.model}")
        wrapper = server.env
        self._write_recording_metadata(wrapper, robot)
        self.prelude_frames[0] = len(wrapper.current_traj_history) - 1
        teleop_utils.restore_robot_start(self.server, self.scene_file, teleop_utils.scene_snapshot(wrapper.scene))
        self.prelude_frames[1] = len(wrapper.current_traj_history) - 1
        self._notify(f"Ready for {self._label(self.segment_ids[0])}. Press and release N to begin.")

    @property
    def waiting_for_ready(self):
        """Tell JoyLo to wait until a new control command matches the robot."""
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
        """Handle N, then start or resume only after GELLO has synchronized."""
        if self.finished:
            return
        server = self.server
        if self.advance_requested:
            self.advance_requested = False
            if server._waiting_to_resume:
                self._begin_ready_handshake()
            elif self.active_segment is not None:
                self._finish_active_segment()

        if not self.waiting_for_ready:
            return
        generation, command = server.session_command_snapshot()
        if generation <= self.awaiting_ready_generation:
            return
        if not teleop_utils.ready_command_is_synced(server, command):
            return

        self.awaiting_ready_generation = None
        # A paused segment retains its entry mark; only a new segment needs one.
        if not self.resume_existing_segment:
            self._start_segment()
        self.resume_existing_segment = False

    def after_step(self):
        """Called after each operator action to retain every Query object event."""
        if self.active_segment == self.session.query_id:
            self._collect_query_events()

    def abort(self, reason="operator stopped collection"):
        """Preserve marks from an interrupted recording and flag it as invalid."""
        self.invalid_reason = self.invalid_reason or reason
        self._write_marks()
        self.finished = True

    # Segment lifecycle: entry -> operator actions -> exit -> next segment

    def _start_segment(self):
        """Validate and mark the entry before accepting operator actions."""
        segment_id = self.segment_ids[self.next_segment_index]
        robot = self.server.robot
        target = self.instance["robot"]
        self._validate_segment_entry(segment_id)
        teleop_utils.require_empty_hands(robot)
        teleop_utils.require_collision_free(robot)
        teleop_utils.require_stable(robot)
        teleop_utils.require_entry_pose(robot, target)
        entry_path = self._state_path(segment_id, "entry")
        write_json(entry_path, teleop_utils.scene_snapshot(self.server.env.scene))
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
        activity = self.session.segments[segment_id]["activity"]
        instruction = self.session.task.instruction_for(activity, self.case_id)
        self._notify(f"Recording {self._label(segment_id)}: {instruction}")

    def _finish_active_segment(self):
        """Validate the exit, save its mark, then finish or prepare the next task."""
        segment_id = self.active_segment
        robot = self.server.robot

        # Failed task conditions or motion allow another N after correction.
        teleop_utils.require_empty_hands(robot)
        teleop_utils.require_collision_free(robot)
        if not teleop_utils.is_stable(robot):
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

        # Save the exit before restoring the robot, keeping its frame intact.
        exit_path = self._state_path(segment_id, "exit")
        end = len(self.server.env.current_traj_history) - 1
        exit_snapshot = teleop_utils.scene_snapshot(self.server.env.scene)
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

        # Preserve the world, restore only the robot, and wait for the next N.
        next_segment = self.segment_ids[self.next_segment_index]
        self.server.pause()
        self.active_segment = None
        restore_start = len(self.server.env.current_traj_history) - 1
        teleop_utils.restore_robot_start(self.server, self.scene_file, exit_snapshot)
        self.transfers[f"transfer_{len(self.transfers)}"] = {
            "kind": "robot_restore",
            "from_segment": segment_id,
            "to_segment": next_segment,
            "restore_frames": [restore_start, len(self.server.env.current_traj_history) - 1],
        }
        self._write_marks()
        self._notify(f"Ready for {self._label(next_segment)}. Press and release N to begin.")

    def _begin_ready_handshake(self):
        """Require a command newer than the one present when N was handled."""
        self.server.resume_control()
        self.awaiting_ready_generation = self.server.session_command_snapshot()[0]
        self.resume_existing_segment = self.active_segment is not None
        self._notify("Waiting for GELLO to sync to the R1Pro. Keep clear until the robot is ready.")

    # Task conditions and per-action Query monitoring

    def _live_bindings(self, segment_id):
        """Resolve frozen BDDL bindings to objects in the currently loaded scene."""
        return {
            instance: self.server.env.scene.object_registry("name", object_name)
            for instance, object_name in self.instance["bindings"][segment_id].items()
        }

    def _validate_segment_entry(self, segment_id):
        """Require task preconditions and preserve the previous segment's result."""
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

    def _collect_query_events(self):
        """Keep per-action events; opening a wrong cabinet invalidates the take."""
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

    # Recording metadata, marks and operator messages

    def _write_recording_metadata(self, wrapper, robot):
        """Record the session identity and validate the shared JoyLo interface."""
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

    def _write_marks(self):
        """Atomically save boundaries, discarded transfers and validity for splitting."""
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

    def _state_path(self, segment_id, boundary):
        return self.state_dir / f"{segment_id}_{boundary}.json"

    def _label(self, segment_id):
        return "Query" if segment_id == self.session.query_id else segment_id.replace("_", " ").title()

    def _notify(self, message):
        print(f"[Memory Bench] {message}")
        if self.server is not None:
            from joylo.gello.utils import og_teleop_utils as utils

            utils.add_status_event(self.server.event_queue, "session", message)


def collect_session(
    session,
    instance_id: int,
    recording_path: Path | None = None,
    *,
    host="127.0.0.1",
    port=6001,
):
    """Build and run a JoyLo server for one prepared session instance."""
    count = session.instance_count
    if instance_id < 1 or instance_id > count:
        raise ValueError(f"instance_id must be in 1..{count}")
    instance_path = session.paths.resolve(f"sessions/{session.session_id}/tro/instance_{instance_id:04d}.json")
    if recording_path is None:
        recording_path = session.paths.resolve(
            f"recordings/{session.session_id}/instance_{instance_id:04d}_recording_0001.hdf5"
        )
    Path(recording_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    # Create a SessionCollector to manage the session instance and recording.
    collector = SessionCollector(session, instance_path, recording_path)

    from joylo.gello.utils.og_teleop_utils import generate_basic_environment_config

    action_frequency = session.action_frequency
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
        "scene_file": collector.scene_file,
        "load_room_types": None,
        "load_room_instances": None,
        "include_robots": False,
        "trav_map_with_objects": False,
    }
    # SessionCollector checks MemoryTask conditions for each K/Q segment.
    # The environment keeps one continuous world throughout the recording.
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
