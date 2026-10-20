"""Record individual subtasks while keeping one live simulation world."""

from copy import deepcopy
from pathlib import Path

import h5py

import omnigibson as og
from omnigibson.envs import HDF5CollectionWrapper
from omnigibson.utils.asset_utils import get_omnigibson_git_hash


class SegmentRecordingWrapper(HDF5CollectionWrapper):
    """Only accepted subtask actions enter the current file's trajectory buffer."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.disable_dump_filters()
        self._clear_segment()

    def create_dataset(self, output_path, env, overwrite=True):
        # The base constructor registers callbacks once; files open only at finish.
        self.hdf5_file = None

    def _clear_segment(self):
        self._is_recording = False
        self.recording_path = None
        self.current_traj_history = []
        self.current_transitions = {}
        self.checkpoint_states = []
        self.checkpoint_step_idxs = []
        self.checkpoint_rollback_trajs = {}
        self.max_state_size = 0
        self.init_metadata = {}
        self.traj_count = 0
        self.step_count = 0

    def begin_segment(self, path, entry, metadata):
        """Seed state zero after preparation and control synchronization complete."""
        if self.is_recording:
            raise RuntimeError("Finish the active subtask before beginning another recording")
        path = Path(path)
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path}")
        self._clear_segment()
        self.recording_path = path
        self._entry_scene = deepcopy(entry)
        self._recording_config = deepcopy(self.env.config)
        self._segment_metadata = deepcopy(metadata)
        # DummyTask keeps one live world; each file still declares its real activity.
        self._recording_config["task"]["activity_name"] = metadata["activity_name"]
        self._recording_start_step = int(self.env.episode_steps[0].item())
        state = og.sim.dump_state(serialized=True)
        self.current_traj_history = [{"state": state.clone(), "state_size": len(state)}]
        self.max_state_size = len(state)
        self._is_recording = True

    def step(self, action, n_render_iterations=1):
        if self.is_recording:
            return super().step(action, n_render_iterations=n_render_iterations)
        # Robot restoration advances physics but contributes no recording frames.
        obs, rewards, terminated, truncated, infos = self.env.step(action, n_render_iterations=n_render_iterations)
        self.current_obs = obs[0]
        return obs[0], rewards[0].item(), terminated[0].item(), truncated[0].item(), infos[0]

    def reset(self):
        """Reset the environment before collection starts."""
        if self.is_recording:
            raise RuntimeError("Finish or discard the active subtask before resetting the environment")
        obs, info = self.env.reset()
        self._clear_segment()
        self.current_obs = obs[0]
        return obs[0], info

    def add_transition_info(self, obj, add=True):
        if self.is_recording:
            super().add_transition_info(obj, add=add)

    def discard_segment(self):
        """Drop the current buffer and any temporary file from a failed save."""
        if self.recording_path is not None:
            self.recording_path.with_suffix(self.recording_path.suffix + ".tmp").unlink(missing_ok=True)
        self._clear_segment()

    def finish_segment(self):
        """Publish a closed file before releasing its buffer or changing the world."""
        if not self.is_recording:
            raise RuntimeError("No active subtask recording")
        count = len(self.current_traj_history) - 1
        if count == 0:
            raise ValueError("A completed subtask must contain at least one action")
        path = self.recording_path
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        # Serialization pads each step dictionary; retain the original buffer on failure.
        trajectory = [dict(step) for step in self.current_traj_history]
        rollbacks = self.checkpoint_rollback_trajs
        self.checkpoint_rollback_trajs = {
            step: [{**branch, "step_data": [dict(item) for item in branch["step_data"]]} for branch in branches]
            for step, branches in rollbacks.items()
        }
        try:
            with h5py.File(temporary, "w") as stream:
                self.hdf5_file = stream
                data = stream.create_group("data")
                self.add_metadata(data, "config", self._recording_config)
                self.add_metadata(data, "scene_file", self._entry_scene)
                self.add_metadata(data, "omnigibson_git_hash", get_omnigibson_git_hash())
                config = self._recording_config
                for name, value in {
                    "fps": config["env"]["rendering_frequency"],
                    "action_frequency": config["env"]["action_frequency"],
                    "control_frequency": config["env"]["action_frequency"],
                    "robot_model": "r1pro",
                    "action_dim": self._segment_metadata["interface"]["action_dim"],
                }.items():
                    self.add_metadata(data, name, value)
                for name, value in self._segment_metadata.items():
                    self.add_metadata(data, name, value)
                data.attrs["n_episodes"] = 1
                data.attrs["n_steps"] = count
                data.attrs["collection_complete"] = True
                self.process_traj_to_dataset(trajectory)
                self.flush_current_file()
            self.hdf5_file = None
            temporary.replace(path)
        finally:
            self.hdf5_file = None
            self.checkpoint_rollback_trajs = rollbacks
        self._clear_segment()
        return path, count

    def flush_current_traj(self):
        raise RuntimeError("Use finish_segment so the subtask is saved before its buffer is released")

    def save_data(self):
        if self.is_recording:
            raise RuntimeError("The collector must finish or discard the active subtask before shutdown")

    def close_dataset(self):
        if self.hdf5_file is not None:
            self.hdf5_file.close()
            self.hdf5_file = None
