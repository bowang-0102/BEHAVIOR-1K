"""Decode accepted simulator trajectories with one reusable recorded-schema environment."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

import h5py

from memory_bench.session import Session
from memory_bench.utils.recordings import recording_metadata
from memory_bench.utils.scene_state import read_json, scene_objects


def _state_schema(value):
    if isinstance(value, dict):
        return {key: _state_schema(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_state_schema(item) for item in value]
    return type(value).__name__


class NormalizationContext:
    """Reuse a simulator only for sources with the same object and serialized-state schema."""

    def __init__(self, session):
        self.session = session
        self.env = None
        self.schema = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self.env is not None:
            import omnigibson as og

            og.clear()
            self.env = None
            self.schema = None

    def _prepare(self, metadata):
        import omnigibson as og
        from omnigibson.envs.data_wrapper import (
            _add_recorded_non_kin_states_to_scene_file,
            _align_scene_object_states_with_recorded_schema,
        )
        from omnigibson.macros import gm

        schema = {
            "objects_info": metadata["scene"]["objects_info"],
            "state": _state_schema(metadata["scene"]["state"]),
            "robots": metadata["config"]["robots"],
            "scene_model": metadata["config"]["scene"]["scene_model"],
        }
        if self.env is not None:
            if schema != self.schema:
                raise ValueError("Normalization batch changed its recorded object/robot/state schema")
            return
        config, scene = deepcopy(metadata["config"]), deepcopy(metadata["scene"])
        _add_recorded_non_kin_states_to_scene_file(scene, [scene])
        config["scene"].update(scene_file=scene, load_room_types=None, load_room_instances=None)
        config["objects"] = []
        config["task"] = {"type": "DummyTask"}
        config["env"]["automatic_reset"] = False
        if og.sim is not None:
            config.setdefault("render", {}).update(viewer_width=og.sim.viewer_width, viewer_height=og.sim.viewer_height)
        for robot in config["robots"]:
            robot["obs_modalities"] = []
        with gm.unlocked():
            gm.ENABLE_TRANSITION_RULES = False
            gm.VISUAL_ONLY = False
        self.env = og.Environment(configs=config)
        _align_scene_object_states_with_recorded_schema(self.env.scene, metadata["scene"])
        self.schema = schema

    def normalize(self, source, output, demo=0, terminal=None):
        metadata = recording_metadata(source, demo)
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with h5py.File(source, "r") as original:
            incoming = original["data"][f"demo_{demo}"]
            serialized = "state_json" not in incoming
            if serialized:
                self._prepare(metadata)
            count = len(incoming["action"])
            state_count = len(incoming["state"] if serialized else incoming["state_json"])
            if state_count not in (count, count + 1):
                raise ValueError("Accepted trajectory needs N pre-action states and at most one terminal state")
            with h5py.File(output, "x") as normalized:
                data = normalized.create_group("data")
                data.attrs["config"] = json.dumps(metadata["config"])
                data.attrs["scene_file"] = json.dumps(metadata["scene"])
                data.attrs["format"] = "memory_bench.state_json.v1"
                data.attrs["n_episodes"] = 1
                data.attrs["n_steps"] = count
                group = data.create_group("demo_0")
                group.attrs["num_samples"] = count
                group.attrs["transitions"] = "{}"
                for key in ("action", "reward", "terminated", "truncated"):
                    group.create_dataset(key, data=incoming[key][:count], compression="lzf")
                states = group.create_dataset("state_json", shape=(count + 1,), dtype=h5py.string_dtype())
                current = deepcopy(metadata["scene"]["state"])
                for frame in range(count + 1):
                    if frame < state_count:
                        if serialized:
                            import torch as th
                            import omnigibson as og

                            flat = th.as_tensor(incoming["state"][frame, : int(incoming["state_size"][frame])])
                            decoded, consumed = og.sim.deserialize(flat)
                            if consumed != len(flat):
                                raise ValueError(f"Recorded state schema mismatch at frame {frame}")
                            scene_objects(current).update(decoded[0]["registry"]["object_registry"])
                            current["registry"]["system_registry"].update(decoded[0]["registry"]["system_registry"])
                        else:
                            current = json.loads(incoming["state_json"][frame])
                    elif terminal is not None:
                        current = read_json(terminal)["state"]
                    else:
                        raise ValueError("Missing post-action terminal state; provide --terminal snapshot")
                    states[frame] = json.dumps(current, default=lambda value: value.tolist(), separators=(",", ":"))
                data.attrs["normalization_complete"] = True
        return output


def normalize_recording(session, source, output, demo=0, terminal=None, context=None):
    """Normalize one source; callers may share a context across one instance's K/Q files."""
    if context is not None:
        return context.normalize(source, output, demo, terminal)
    with NormalizationContext(session) as context:
        return context.normalize(source, output, demo, terminal)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument("--demo", type=int, default=0)
    parser.add_argument("--terminal")
    args = parser.parse_args()
    session = Session.load(args.config)
    session.paths.activate()
    normalize_recording(
        session,
        session.paths.resolve(args.source),
        session.paths.resolve(args.output),
        args.demo,
        session.paths.resolve(args.terminal) if args.terminal else None,
    )


if __name__ == "__main__":
    main()
