"""Compare full and filtered collection on the same prepared world, without GELLO.

Run in the behavior environment with OMNIGIBSON_HEADLESS=1 and OMNIGIBSON_GPU_ID set.
Only benchmark recordings under --output-dir are written; prepared inputs are read-only.
"""

import argparse
from collections import defaultdict
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
from types import MethodType

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "joylo"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path,
        default=ROOT / "memory_bench/configs/mb_retrieve_chocolate_from_top_cabinet.yaml",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=150)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)

    from memory_bench.session import Session
    from memory_bench.utils.instance_scene import load_instance_scene

    session = Session.load(args.config)
    _, prepared = load_instance_scene(session, session.paths.session_dir / "tro/instance_0001.json")
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)

    import torch as th
    import omnigibson as og
    from omnigibson.envs import HDF5CollectionWrapper
    from omnigibson.utils.config_utils import TorchEncoder
    from gello.utils.og_teleop_utils import apply_omnigibson_macros, generate_basic_environment_config, generate_robot_config
    from gello.utils.og_teleop_cfg import DISABLED_TRANSITION_RULES
    from memory_bench.collection.recording_wrapper import SegmentRecordingWrapper
    from memory_bench.collection.teleop_utils import scene_snapshot
    from memory_bench.navigation import _base_action
    from memory_bench.utils.scene_state import require_objects_match

    apply_omnigibson_macros()
    for rule in DISABLED_TRANSITION_RULES:
        rule.ENABLED = False
    config = generate_basic_environment_config("r1pro", "robot")
    config["env"].update(
        action_frequency=session.action_frequency, rendering_frequency=session.action_frequency,
        physics_frequency=session.action_frequency * 4, automatic_reset=False,
    )
    config["scene"] = {
        "type": "InteractiveTraversableScene", "scene_model": session.scene_model,
        "scene_file": deepcopy(prepared), "include_robots": False,
        "load_room_types": None, "load_room_instances": None, "trav_map_with_objects": False,
    }
    config["task"] = {"type": "DummyTask"}
    robot_config = generate_robot_config("r1pro", "robot")
    robot_config.update(position=prepared["robot"]["position"], orientation=prepared["robot"]["orientation"])
    config["robots"] = [robot_config]
    print("Loading the full prepared scene", flush=True)
    env = og.Environment(configs=config)
    print(f"Loaded {len(env.scene.objects)} objects; warming up physics", flush=True)
    wrapper = SegmentRecordingWrapper(
        env=env, output_path=str(args.output_dir), enable_dump_filters=False,
        viewport_camera_path=og.sim.viewer_camera.active_camera_path, keep_checkpoint_rollback_data=True,
    )
    robot = env.scene.robots[0]
    robot.reset()
    robot.set_position_orientation(prepared["robot"]["position"], prepared["robot"]["orientation"])
    robot.keep_still()
    hold = _base_action(robot)
    for _ in range(90):
        wrapper.step(hold)
    initial = scene_snapshot(env.scene)
    metadata = {
        "activity_name": session.segments[session.key_ids[0]]["activity"],
        "interface": {"action_dim": robot.action_dim},
    }
    results = []
    # Compare the same simulation step, not approximate task-boundary equivalence.
    tolerance = {"ignored_fields": [], "numeric": {"default": 1e-6}}

    # Both paths see the same world and action sequence. Reverse order on the second pair.
    run_modes = () if args.validate_only else ("full", "filtered", "filtered", "full")
    for run, mode in enumerate(run_modes):
        env.scene.restore(deepcopy(initial))
        wrapper.begin_segment(args.output_dir / f"{run}_{mode}.hdf5", initial, metadata)
        parse = HDF5CollectionWrapper._parse_step_data if mode == "full" else SegmentRecordingWrapper._parse_step_data
        parse_times = []

        def timed_parse(self, *values):
            started = time.perf_counter()
            result = parse(self, *values)
            parse_times.append(time.perf_counter() - started)
            return result

        wrapper._parse_step_data = MethodType(timed_parse, wrapper)
        step_times = []
        state_sizes = []
        for frame in range(args.frames + 15):
            action = hold.clone()
            for arm in robot.arm_names:
                action[robot.arm_action_idx[arm][-1]] += 0.12 * math.sin(frame * 2 * math.pi / 90)
            started = time.perf_counter()
            wrapper.step(action)
            elapsed = time.perf_counter() - started
            if frame >= 15:
                step_times.append(elapsed)
                state_sizes.append(wrapper.current_traj_history[-1]["state_size"])
        result = {
            "mode": mode, "run": run, "frames": args.frames,
            "step_ms": statistics.mean(step_times) * 1000,
            "save_ms": statistics.mean(parse_times[15:]) * 1000,
            "steps_per_second": len(step_times) / sum(step_times),
            "state_floats": statistics.mean(state_sizes),
            "step_p95_ms": sorted(step_times)[int(len(step_times) * 0.95)] * 1000,
        }
        results.append(result)
        print(json.dumps(result), flush=True)
        wrapper.discard_segment()

    # Untimed comparison against full live snapshots catches lost sleeping/dirty states.
    env.scene.restore(deepcopy(initial))
    wrapper.begin_segment(args.output_dir / "readback.hdf5", initial, metadata)
    wrapper._parse_step_data = MethodType(SegmentRecordingWrapper._parse_step_data, wrapper)
    carried = deepcopy(initial["state"])
    for frame in range(60):
        action = hold.clone()
        for arm in robot.arm_names:
            action[robot.arm_action_idx[arm][-1]] += 0.12 * math.sin(frame * 2 * math.pi / 90)
        wrapper.step(action)
        decoded, _ = og.sim.deserialize(wrapper.current_traj_history[-1]["state"])
        decoded = json.loads(json.dumps(decoded[0], cls=TorchEncoder))
        carried["registry"]["object_registry"].update(decoded["registry"]["object_registry"])
        expected = scene_snapshot(env.scene)
        try:
            require_objects_match(
                carried, expected, set(expected["state"]["registry"]["object_registry"]),
                tolerance, f"Filtered live readback frame {frame}",
            )
        except ValueError:
            (args.output_dir / "readback_mismatch.json").write_text(
                json.dumps({"frame": frame, "actual": carried, "expected": expected})
            )
            raise
    wrapper.finish_segment()

    grouped = defaultdict(list)
    for result in results:
        grouped[result["mode"]].append(result)
    means = {mode: {key: statistics.mean(row[key] for row in rows)
                    for key in ("step_ms", "save_ms", "steps_per_second", "state_floats")}
             for mode, rows in grouped.items()}
    report = {
        "config": str(args.config), "gpu": os.environ.get("OMNIGIBSON_GPU_ID"),
        "objects": len(env.scene.objects), "rendering": "headless; no GELLO, ghost or GUI status updates",
        "runs": results, "means": means,
        "live_readback_frames": 60,
    }
    if results:
        report["step_speedup"] = means["full"]["step_ms"] / means["filtered"]["step_ms"]
        report["save_speedup"] = means["full"]["save_ms"] / means["filtered"]["save_ms"]
    (args.output_dir / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)
    og.shutdown()


if __name__ == "__main__":
    main()
