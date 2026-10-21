"""Collect Keys and Query in separate files while preserving one live world.

SessionCollector owns segment sequencing, task checks and the run index.
teleop_utils owns controller synchronization, robot restoration and physical checks.
"""

import argparse
from copy import deepcopy
from pathlib import Path

from memory_bench.collection import teleop_utils
from memory_bench.utils.instance_scene import load_instance_scene
from memory_bench.utils.scene_state import file_hash, read_json, write_json_atomic


class SessionCollector:
    """Advance a prepared instance through Key(s) -> Query without resetting the world.

    JoyLo queues button input, calls process() before each simulation step,
    and after_step() after each operator action. Left finishes a segment and
    restores the next start; Right starts teleoperation after GELLO synchronizes.
    """

    def __init__(self, session, instance_path: Path, recording_dir: Path):
        self.session = session
        self.instance_path = Path(instance_path)
        self.instance, self.scene_file = load_instance_scene(session, self.instance_path)
        self.recording_dir = Path(recording_dir).expanduser().resolve()
        self.instance_sha256 = file_hash(self.instance_path)
        self.run_dir = None
        self.manifest_path = None
        self.segment_ids = [*session.key_ids, session.query_id]
        self.case_id = self.instance["case_id"]
        self.server = None

        # Control flow: no active segment means we are between tasks. While
        # waiting for a fresh GELLO command, JoyLo holds simulation stepping.
        self.active_segment = None
        self.segment_index = 0
        self.finish_requested = False
        self.start_requested = False
        self.next_instance_requested = False
        self.awaiting_ready_generation = None
        # A resume preserves the current segment's entry mark.
        self.resume_existing_segment = False
        self.run_finished = False

        # File-local events and the small whole-run index.
        self.query_open_state = None
        self.object_events = []
        self.segments = {}
        self.transfers = []
        self.invalid_reason = None
        self.interface = None

        if self.instance["instance_id"] <= 0:
            raise ValueError("instance_id must be a positive integer")
        if set(self.segment_ids) - set(self.instance["bindings"]):
            raise ValueError("Instance is missing one or more Key/Query BDDL bindings")
        if not self.instance["validation"].get("shared_initial_robot_state"):
            raise ValueError("Reprepare instances with a shared first-Key robot state before collection")
        if self.recording_dir.suffix == ".hdf5":
            raise ValueError("Session recording output must be a directory, not an HDF5 filename")

    # JoyLo lifecycle callbacks

    def restore_initial_scene(self, scene):
        """Install the frozen full-scene state before HDF5 metadata is created."""
        scene.restore(deepcopy(self.scene_file), update_initial_file=True)

    def bind_server(self, server):
        """Attach JoyLo and restore the instance's common Key/Query robot start."""
        self.server = server
        server._session_collector = self
        if server.robot.model != "r1pro":
            raise ValueError(f"Session collection requires R1Pro, got {server.robot.model}")
        self.recording_dir.mkdir(parents=True, exist_ok=True)
        runs = [
            int(path.name.removeprefix("run_"))
            for path in self.recording_dir.glob("run_*")
            if path.is_dir() and path.name.removeprefix("run_").isdigit()
        ]
        run_id = max(runs, default=0) + 1
        self.run_dir = self.recording_dir / f"run_{run_id:04d}"
        self.run_dir.mkdir()
        self.manifest_path = self.run_dir / "manifest.json"
        server._session_stop_requested = False
        self.clear_checkpoints()
        self._read_interface(server.env, server.robot)
        server.pause()
        server.load_collection_instance(self.scene_file)
        if self.run_finished:
            return
        self._write_manifest()
        self._notify(
            f"Instance {self.instance['instance_id']}: ready for {self._label(self.segment_ids[0])}. "
            "Press Right or X to begin."
        )

    @property
    def waiting_for_ready(self):
        """Tell JoyLo to wait until a new control command matches the robot."""
        return self.awaiting_ready_generation is not None

    def cancel_ready(self):
        """A pause cancels a pending synchronization handshake."""
        self.awaiting_ready_generation = None
        self.resume_existing_segment = False
        self.start_requested = False

    def process(self):
        """Finish and start are separate actions; each resume synchronizes GELLO."""
        if self.server._session_stop_requested:
            return
        if self.next_instance_requested:
            self.next_instance_requested = False
            self._next_instance()
            return
        if self.run_finished:
            return
        server = self.server
        if self.finish_requested:
            self.finish_requested = False
            self.start_requested = False
            if self.active_segment is not None and not self.waiting_for_ready:
                self._finish_active_segment()
            else:
                self._notify("No running subtask to finish. Press Right to start the prepared subtask.")

        if self.run_finished:
            return
        if self.start_requested:
            self.start_requested = False
            if self.active_segment in self.segments:
                self._notify("The subtask file is saved. Press Left again to finish publishing its index.")
            elif server._waiting_to_resume and not self.waiting_for_ready:
                server.resume_control()
                self.awaiting_ready_generation = server.session_command_snapshot()[0]
                self.resume_existing_segment = self.active_segment is not None
                self._notify("Waiting for GELLO to sync to the R1Pro.")

        if not self.waiting_for_ready:
            return
        generation, command = server.session_command_snapshot()
        if generation <= self.awaiting_ready_generation:
            return
        if not teleop_utils.ready_command_is_synced(server, command, resume_existing=self.resume_existing_segment):
            return

        self.awaiting_ready_generation = None
        # A paused segment retains its entry mark; only a new segment needs one.
        if not self.resume_existing_segment:
            try:
                self._start_segment()
            except RuntimeError as error:
                server.pause()
                self._notify(f"Cannot start the prepared subtask: {error}")
        self.resume_existing_segment = False

    def clear_checkpoints(self):
        """Checkpoints never cross a subtask boundary."""
        self.server.env.checkpoint_states.clear()
        self.server.env.checkpoint_step_idxs.clear()
        self.server._rollback_checkpoint_idx = None
        self.server._should_update_checkpoint = False
        self.server._frame_counter = 0

    def after_rollback(self):
        """Discard Query events from the removed actions and rebuild its monitor."""
        frame = len(self.server.env.current_traj_history) - 1
        self.object_events = [event for event in self.object_events if event["frame"] < frame]
        forbidden = [event for event in self.object_events if event.get("forbidden", False)]
        self.invalid_reason = f"wrong cabinet opened: {forbidden[0]['bddl_instance']}" if forbidden else None
        if self.active_segment == self.session.query_id:
            objects = self._live_bindings(self.session.query_id)
            _, self.query_open_state = self.session.task.query_events(objects, case_id=self.case_id)
        self.finish_requested = False
        self.cancel_ready()
        self.server._rollback_checkpoint_idx = None
        self._write_manifest()
        self._notify("Rolled back within the current subtask. Press Right or X to resume.")

    def after_step(self):
        """Called after each operator action to retain every Query object event."""
        if self.active_segment == self.session.query_id:
            self._collect_query_events()

    def abort(self, reason="operator stopped collection"):
        """Discard the unfinished subtask and mark the run as aborted."""
        if self.run_finished:
            return
        self.invalid_reason = self.invalid_reason or reason
        self.server.env.discard_segment()
        self._write_manifest(status="aborted")
        self.run_finished = True

    def _next_instance(self):
        """Advance in the same simulator, closing only the current instance's run."""
        instance_id = self.instance["instance_id"] + 1
        if instance_id > self.session.instance_count:
            self._notify("No more prepared instances. Finish this instance or press Esc to exit.")
            return
        try:
            next_collector = SessionCollector(
                self.session,
                self.instance_path.with_name(f"instance_{instance_id:04d}.json"),
                self.recording_dir.with_name(f"instance_{instance_id:04d}"),
            )
        except (OSError, ValueError) as error:
            self._notify(f"Cannot load instance {instance_id}: {error}")
            return
        self.abort("operator switched to the next instance")
        next_collector.bind_server(self.server)

    # Segment lifecycle: entry -> operator actions -> exit -> next segment

    def _start_segment(self):
        """Validate and mark the entry before accepting operator actions."""
        segment_id = self.segment_ids[self.segment_index]
        robot = self.server.robot
        target = self.instance["robot"]
        self._validate_segment_entry(segment_id)
        teleop_utils.require_empty_hands(robot)
        teleop_utils.require_collision_free(robot)
        teleop_utils.require_stable(robot)
        teleop_utils.require_entry_pose(robot, target)
        entry_path = self._state_path(segment_id, "entry")
        entry = teleop_utils.scene_snapshot(self.server.env.scene)
        write_json_atomic(entry_path, entry)
        self.server.env.begin_segment(
            self.run_dir / f"{segment_id}.hdf5",
            entry,
            {
                "session_id": self.session.session_id,
                "instance_id": self.instance["instance_id"],
                "case_id": self.case_id,
                "segment_id": segment_id,
                "activity_name": self.session.segments[segment_id]["activity"],
                "run_id": self.run_dir.name,
                "interface": self.interface,
            },
        )
        self.active_segment = segment_id
        self.object_events = []
        if segment_id == self.session.query_id:
            objects = self._live_bindings(self.session.query_id)
            _, self.query_open_state = self.session.task.query_events(objects, case_id=self.case_id)
        self.clear_checkpoints()
        self.server.env.update_checkpoint()
        self._write_manifest()
        activity = self.session.segments[segment_id]["activity"]
        instruction = self.session.task.instruction_for(activity, self.case_id)
        self._notify(f"Recording {self._label(segment_id)}: {instruction}")

    def _finish_active_segment(self):
        """Close the accepted file before publishing it or restoring the robot."""
        segment_id = self.active_segment
        robot = self.server.robot
        exit_path = self._state_path(segment_id, "exit")
        if segment_id not in self.segments:
            # Failed boundary checks leave teleoperation available for correction.
            try:
                teleop_utils.require_empty_hands(robot)
                teleop_utils.require_collision_free(robot)
                teleop_utils.require_stable(robot)
            except RuntimeError as error:
                self._notify(f"Cannot finish {self._label(segment_id)}: {error}")
                return
            objects = self._live_bindings(segment_id)
            if not self.session.task.check_segment(
                segment_id, self.session.segments[segment_id]["role"], objects, case_id=self.case_id
            ):
                self._notify(f"{self._label(segment_id)} postcondition failed; continue teleoperation and retry Left.")
                return
            if segment_id == self.session.query_id and self.invalid_reason is not None:
                self._notify(f"Query contains a forbidden object event: {self.invalid_reason}. Roll back to correct it.")
                return
            self.server.pause()
            try:
                exit_snapshot = teleop_utils.scene_snapshot(self.server.env.scene)
                write_json_atomic(exit_path, exit_snapshot)
                path, count = self.server.env.finish_segment()
            except (OSError, ValueError) as error:
                self._notify(f"Subtask save failed: {error}. Buffer retained; press Left to retry.")
                return
            self.segments[segment_id] = {
                "hdf5": self.session.paths.relative(path),
                "demo": 0,
                "frames": [0, count],
                "states": {
                    "directory": self.session.paths.relative(self.run_dir),
                    "entry": self._state_path(segment_id, "entry").name,
                    "exit": exit_path.name,
                },
                "validation": {
                    "entry": True, "exit": True, "empty_hands": True,
                    "stable": True, "collision_free": True,
                    "object_events": list(self.object_events),
                },
            }
        final_segment = self.segment_index + 1 == len(self.segment_ids)
        try:
            # A failed index write is retryable without rewriting the closed file.
            record = self.segments[segment_id]
            record["sha256"] = file_hash(self.session.paths.resolve(record["hdf5"]))
            self._write_manifest(status="complete" if final_segment else "in_progress")
        except OSError as error:
            self._notify(f"Subtask file saved but index update failed: {error}. Press Left to retry.")
            return
        self.segment_index += 1
        self.active_segment = None
        self.clear_checkpoints()
        if final_segment:
            self.run_finished = True
            self.server._session_stop_requested = self.instance["instance_id"] == self.session.instance_count
            self._notify(f"Instance {self.instance['instance_id']} complete. Press Home for the next instance or Esc to exit.")
            return
        next_segment = self.segment_ids[self.segment_index]
        exit_snapshot = read_json(exit_path)
        teleop_utils.restore_robot_start(self.server, self.scene_file, exit_snapshot)
        self.transfers.append({
            "from_segment": segment_id,
            "to_segment": next_segment,
            "validation": {"nonrobot_world_preserved": True, "entry_pose": True},
        })
        self._write_manifest()
        self._notify(f"Ready for {self._label(next_segment)}. Press Right or keyboard X to begin.")

    # Task conditions and per-action Query monitoring

    def _live_bindings(self, segment_id):
        """Resolve frozen BDDL bindings to objects in the currently loaded scene."""
        # Cached agent names can differ from the configured robot name.
        return {
            instance: (
                self.server.robot
                if instance.startswith("agent.n.")
                else self.server.env.scene.object_registry("name", object_name)
            )
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

    # Recording identity and operator messages

    def _read_interface(self, wrapper, robot):
        """Validate the JoyLo interface once and embed it in every subtask file."""
        config = wrapper.env.config
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

    def _write_manifest(self, status="in_progress"):
        """Publish file identities; frame boundaries within a joint file are unnecessary."""
        run = {
            "format": "memory_bench.collection_run.v1",
            "run_id": self.run_dir.name,
            "session_id": self.session.session_id,
            "instance_id": self.instance["instance_id"],
            "case_id": self.case_id,
            "instance": self.session.paths.relative(self.instance_path),
            "instance_sha256": self.instance_sha256,
            "status": status,
            "segment_ids": self.segment_ids,
            "segments": self.segments,
            "transfers": self.transfers,
        }
        if self.invalid_reason:
            run["invalid_reason"] = self.invalid_reason
        write_json_atomic(self.manifest_path, run)

    def _state_path(self, segment_id, boundary):
        return self.run_dir / f"{segment_id}_{boundary}.json"

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
    recording_dir: Path | None = None,
    *,
    host="127.0.0.1",
    port=6001,
):
    """Start collection at one prepared instance; Home advances without relaunching."""
    count = session.instance_count
    if instance_id < 1 or instance_id > count:
        raise ValueError(f"instance_id must be in 1..{count}")
    instance_path = session.paths.resolve(f"sessions/{session.session_id}/tro/instance_{instance_id:04d}.json")
    if recording_dir is None:
        recording_dir = session.paths.resolve(f"recordings/{session.session_id}/instance_{instance_id:04d}")
    # Create a SessionCollector to manage the session instance and recording.
    collector = SessionCollector(session, instance_path, recording_dir)

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
        "scene_file": deepcopy(collector.scene_file),
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
        recording_path=str(collector.recording_dir),
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
    parser.add_argument("--recording-dir", type=Path)
    args = parser.parse_args()

    from memory_bench.session import Session

    session = Session.load(args.config)
    session.paths.activate()
    collect_session(session, args.instance_id, args.recording_dir)


if __name__ == "__main__":
    main()
