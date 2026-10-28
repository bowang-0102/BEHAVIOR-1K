"""Measure production collection for one prepared session scene, without GELLO.

Run in the behavior environment with OMNIGIBSON_HEADLESS=1 and OMNIGIBSON_GPU_ID set.
Run separately with the baseline and cropped session configs, then pass the first
report to --baseline-report. Only --output-dir is written; prepared inputs are read-only.
"""

import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time
import traceback
from types import MethodType

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "joylo"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path,
        default=ROOT / "memory_bench/configs/mb_retrieve_baking_soda_from_top_cabinet.yaml",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=150)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.frames <= 0:
        parser.error("--frames must be positive")
    if args.validate_only and args.baseline_report:
        parser.error("--baseline-report requires timed runs")
    args.output_dir.mkdir(parents=True, exist_ok=False)

    og = None
    try:
        from memory_bench.session import Session
        from memory_bench.utils.instance_scene import load_instance_scene

        session = Session.load(args.config)
        _, prepared = load_instance_scene(session, session.paths.session_dir / "tro/instance_0001.json")
        os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)

        import omnigibson as og
        from omnigibson.utils.config_utils import TorchEncoder
        from gello.utils.og_teleop_utils import (
            apply_omnigibson_macros, generate_basic_environment_config, generate_robot_config,
        )
        from gello.utils.og_teleop_cfg import DISABLED_TRANSITION_RULES
        from memory_bench.collection.recording_wrapper import SegmentRecordingWrapper
        from memory_bench.collection.teleop_utils import scene_snapshot
        from memory_bench.navigation import _base_action
        from memory_bench.utils.scene_state import require_objects_match, write_json

        apply_omnigibson_macros()
        for rule in DISABLED_TRANSITION_RULES:
            rule.ENABLED = False
        config = generate_basic_environment_config("r1pro", "robot")
        config["objects"] = []
        config["env"].update(
            action_frequency=session.action_frequency, rendering_frequency=session.action_frequency,
            physics_frequency=session.action_frequency * 4, automatic_reset=False,
            flatten_action_space=False, flatten_obs_space=False,
        )
        config["scene"] = {
            "type": "InteractiveTraversableScene", "scene_model": session.scene_model,
            "scene_file": deepcopy(prepared), "include_robots": False, "trav_map_with_objects": False,
        }
        config["task"] = {"type": "DummyTask"}
        robot_config = generate_robot_config("r1pro", "robot")
        robot_config.update(position=prepared["robot"]["position"], orientation=prepared["robot"]["orientation"])
        config["robots"] = [robot_config]
        print(f"Loading prepared scene from {args.config}", flush=True)
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

        # Every run restores the same world and executes the same production collection path.
        for run in range(0 if args.validate_only else 2):
            env.scene.restore(deepcopy(initial))
            path = args.output_dir / f"run_{run}.hdf5"
            wrapper.begin_segment(path, initial, metadata)
            parse_times = []

            def timed_parse(self, *values):
                started = time.perf_counter()
                result = SegmentRecordingWrapper._parse_step_data(self, *values)
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
            # HDF5 stores pre-action states; the sidecar supplies the actual terminal world.
            write_json(path.with_name(f"{path.stem}_exit.json"), scene_snapshot(env.scene))
            wrapper.finish_segment()
            result = {
                "run": run, "frames": args.frames, "recording": str(path),
                "step_ms": statistics.mean(step_times) * 1000,
                "save_ms": statistics.mean(parse_times[15:]) * 1000,
                "steps_per_second": len(step_times) / sum(step_times),
                "state_floats": statistics.mean(state_sizes),
                "step_p95_ms": sorted(step_times)[int(len(step_times) * 0.95)] * 1000,
            }
            results.append(result)
            print(json.dumps(result), flush=True)

        # Compare each reconstructed state with a full snapshot from the same live step.
        tolerance = {"ignored_fields": [], "numeric": {"default": 1e-6}}
        env.scene.restore(deepcopy(initial))
        path = args.output_dir / "readback.hdf5"
        wrapper.begin_segment(path, initial, metadata)
        wrapper._parse_step_data = MethodType(SegmentRecordingWrapper._parse_step_data, wrapper)
        carried = deepcopy(initial["state"])
        for frame in range(60):
            action = hold.clone()
            for arm in robot.arm_names:
                action[robot.arm_action_idx[arm][-1]] += 0.12 * math.sin(frame * 2 * math.pi / 90)
            wrapper.step(action)
            recorded = wrapper.current_traj_history[-1]
            flat = recorded["state"][:recorded["state_size"]]
            decoded, consumed = og.sim.deserialize(flat)
            if consumed != len(flat):
                raise ValueError(f"Recorded state schema mismatch at live readback frame {frame}")
            decoded = json.loads(json.dumps(decoded[0], cls=TorchEncoder))
            carried["registry"]["object_registry"].update(decoded["registry"]["object_registry"])
            carried["registry"]["system_registry"].update(decoded["registry"]["system_registry"])
            expected = scene_snapshot(env.scene)
            try:
                require_objects_match(
                    carried, expected, set(expected["state"]["registry"]["object_registry"]),
                    tolerance, f"Live readback frame {frame}",
                )
            except ValueError:
                write_json(args.output_dir / "readback_mismatch.json", {
                    "frame": frame, "actual": carried, "expected": expected,
                })
                raise
        write_json(path.with_name(f"{path.stem}_exit.json"), expected)
        wrapper.finish_segment()

        means = {
            key: statistics.mean(row[key] for row in results)
            for key in ("step_ms", "save_ms", "steps_per_second", "state_floats", "step_p95_ms")
        } if results else {}
        report = {
            "config": str(args.config.resolve()), "gpu": os.environ.get("OMNIGIBSON_GPU_ID"),
            "objects": len(env.scene.objects), "action_frequency": session.action_frequency,
            "rendering": "headless; no GELLO, ghost or GUI status updates",
            "timing": "step includes state capture; save measures per-step state capture, excludes HDF5 file writing",
            "frames_per_run": args.frames, "warmup_frames": 15,
            "runs": results, "means": means, "live_readback_frames": 60,
        }
        if args.baseline_report:
            baseline = json.loads(args.baseline_report.read_text())
            for key in ("frames_per_run", "warmup_frames", "action_frequency", "rendering"):
                if baseline[key] != report[key]:
                    raise ValueError(f"Baseline report uses a different {key}")
            report["baseline"] = {"report": str(args.baseline_report.resolve()), "config": baseline["config"]}
            report["speedups"] = {
                "step": baseline["means"]["step_ms"] / means["step_ms"],
                "save": baseline["means"]["save_ms"] / means["save_ms"],
                "steps_per_second": means["steps_per_second"] / baseline["means"]["steps_per_second"],
            }
        write_json(args.output_dir / "report.json", report)
        print(json.dumps(report, indent=2), flush=True)
    except Exception:
        (args.output_dir / "failure.json").write_text(json.dumps({
            "config": str(args.config), "traceback": traceback.format_exc(),
        }, indent=2))
        raise
    finally:
        # Preserve failures even when initialization stopped before a SimulationApp existed.
        if og is not None:
            og.shutdown(due_to_signal=True)


if __name__ == "__main__":
    main()
