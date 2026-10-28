"""Profile the real exit -> bridge -> entry navigation on GPU 1 by default.

python memory_bench/tools/debug/profile_nagigation.py --output-dir /tmp/navigation_profile
Use --render for the optional replay video and --cpu-profile for Python call statistics.
"""

import argparse
from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
import cProfile
from dataclasses import replace
import faulthandler
from functools import wraps
import json
import os
from pathlib import Path
import pstats
import sys
import threading
import time
import traceback


DIRECTORY = Path(__file__).resolve().parent
REPOSITORY = DIRECTORY.parents[2]
sys.path.insert(0, str(REPOSITORY))
sys.dont_write_bytecode = True

# Parameters: previous task exit -> next task entry.
CONFIG = REPOSITORY / "memory_bench/configs/mb_retrieve_baking_soda_from_top_cabinet.yaml"
RAW_DATA = Path("/data/bwang/datasets/2026-challenge-rawdata")
FROM_SEGMENT, FROM_HDF5, FROM_DEMO = "intervent_1", RAW_DATA / "task-0000/episode_00000010.hdf5", 0
TO_SEGMENT, TO_HDF5, TO_DEMO = "intervent_2", RAW_DATA / "task-0022/episode_00220020.hdf5", 0
GPU = 1
RESOLUTION = 240


class NavigationSampleComplete(BaseException):
    """Stop a diagnostic sample without treating a partial bridge as accepted."""


class NavigationTiming:
    def __init__(self, output, settings):
        self.output = output
        self.settings = settings
        self.started = time.perf_counter()
        self.phase = "setup"
        self.stack = []
        self.totals = defaultdict(lambda: {"calls": 0, "total_s": 0.0, "self_s": 0.0, "max_s": 0.0})
        self.restore = []
        self.main_thread = threading.get_ident()
        self.steps = defaultdict(int)
        self.scene = {}
        self.last_progress = self.started
        self.error = None
        self.manifest = None
        self.sample_complete = False
        self.profiler = cProfile.Profile() if settings["cpu_profile"] else None

    @contextmanager
    def measure(self, name, phase=None):
        previous_phase = self.phase
        if phase is not None:
            self.phase = phase
            print(f"TIMING start {phase}", flush=True)
        entry = {"name": f"{self.phase}/{name}", "started": time.perf_counter(), "children_s": 0.0}
        self.stack.append(entry)
        try:
            yield
        finally:
            elapsed = time.perf_counter() - entry["started"]
            self.stack.pop()
            if self.stack:
                self.stack[-1]["children_s"] += elapsed
            row = self.totals[entry["name"]]
            row["calls"] += 1
            row["total_s"] += elapsed
            row["self_s"] += elapsed - entry["children_s"]
            row["max_s"] = max(row["max_s"], elapsed)
            if name in ("collection_step", "world_guard"):
                if name == "collection_step":
                    self.steps[self.phase] += 1
                with (self.output / "steps.jsonl").open("a") as stream:
                    stream.write(json.dumps({
                        "phase": self.phase, "step": self.steps[self.phase],
                        "stage": name, "wall_s": elapsed,
                    }) + "\n")
            now = time.perf_counter()
            if phase is not None or now - self.last_progress >= 5:
                largest = sorted(self.totals.items(), key=lambda item: -item[1]["self_s"])[:4]
                print(
                    f"TIMING {entry['name']}={elapsed:.3f}s; steps={dict(self.steps)}; "
                    + ", ".join(f"{key} self={value['self_s']:.2f}s" for key, value in largest),
                    flush=True,
                )
                self.last_progress = now
                self.save(complete=False, save_cpu=False)
            self.phase = previous_phase

    def timed(self, function, name):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if threading.get_ident() != self.main_thread:
                return function(*args, **kwargs)
            with self.measure(name):
                return function(*args, **kwargs)

        return wrapped

    def patch(self, owner, name, replacement):
        self.restore.append((owner, name, name in vars(owner), getattr(owner, name)))
        setattr(owner, name, replacement)

    def install(self):
        import omnigibson as og
        from omnigibson import envs
        from omnigibson.macros import gm
        from omnigibson.utils import motion_planning_utils
        from memory_bench import navigation
        from memory_bench.episodes import generate_bridges
        from memory_bench.utils import navigation_map

        original_environment = envs.Environment

        @wraps(original_environment)
        def environment(*args, **kwargs):
            with self.measure("environment_load", phase="scene_load"):
                env = original_environment(*args, **kwargs)
            self.scene = {
                "objects": len(env.scene.objects), "action_frequency": env.config["env"]["action_frequency"],
                "gpu_dynamics": gm.USE_GPU_DYNAMICS,
            }
            self.patch(env, "step", self.timed(env.step, "environment_step"))
            for name in ("restore", "load_state", "dump_state"):
                self.patch(env.scene, name, self.timed(getattr(env.scene, name), f"scene_{name}"))
            for name in (
                "step", "step_physics", "render", "_non_physics_step", "dump_state", "serialize", "sync_physx_to_fabric",
            ):
                self.patch(og.sim, name, self.timed(getattr(og.sim, name), f"sim_{name}"))
            print(f"TIMING scene={self.scene}", flush=True)
            return env

        self.patch(envs, "Environment", environment)
        for name in ("step", "_parse_step_data", "save_data"):
            label = {"step": "collection_step", "_parse_step_data": "record_state", "save_data": "hdf5_save"}[name]
            wrapped = self.timed(getattr(envs.HDF5CollectionWrapper, name), label)
            if name == "step":
                def sampled_step(*args, _step=wrapped, **kwargs):
                    limit = self.settings["navigation_steps"]
                    if self.phase == "navigation" and limit > 0 and self.steps[self.phase] >= limit:
                        raise NavigationSampleComplete()
                    return _step(*args, **kwargs)

                wrapped = sampled_step
            self.patch(envs.HDF5CollectionWrapper, name, wrapped)
        for name in ("_scene_snapshot", "_assert_world_preserved"):
            self.patch(generate_bridges, name, self.timed(getattr(generate_bridges, name), name))
        for name in ("astar", "detect_robot_collision_in_sim"):
            self.patch(motion_planning_utils, name, self.timed(getattr(motion_planning_utils, name), name))
        for name in (
            "forward_navigation_map", "forward_route_clear", "rotation_clear", "braking_motion_clear", "footprint_clear",
        ):
            self.patch(navigation_map, name, self.timed(getattr(navigation_map, name), name))

        joint_moves = 0
        for name in ("move_joints", "drive_to_pose"):
            original = getattr(navigation, name)

            def motion(*args, _function=original, _name=name, **kwargs):
                nonlocal joint_moves
                if _name == "move_joints":
                    phase = "stow_joints" if joint_moves == 0 else "restore_joints"
                    joint_moves += 1
                else:
                    phase = "navigation"
                # Retain the actual production guard and all collision checks.
                kwargs["world_guard"] = self.timed(kwargs["world_guard"], "world_guard")
                with self.measure(_name, phase=phase):
                    return _function(*args, **kwargs)

            self.patch(navigation, name, motion)

        original_clear = og.clear

        def clear(*args, **kwargs):
            # Teardown can take minutes; persist completed measurements before it starts.
            try:
                self.save(complete=False)
            finally:
                with self.measure("clear", phase="cleanup"):
                    original_clear(*args, **kwargs)

        self.patch(og, "clear", clear)

    def save(self, complete, save_cpu=True):
        elapsed = time.perf_counter() - self.started
        summary = {
            "settings": self.settings, "scene": self.scene, "complete": complete,
            "run_wall_s": elapsed, "steps": dict(self.steps), "timings": dict(self.totals),
            "active": [entry["name"] for entry in self.stack], "error": self.error,
            "sample_complete": self.sample_complete,
            "motion": None if self.manifest is None else self.manifest["motion"],
        }
        (self.output / "summary.json").write_text(json.dumps(summary, indent=2))
        if self.profiler is not None and save_cpu:
            self.profiler.dump_stats(str(self.output / "cpu.prof"))
            with (self.output / "cpu.txt").open("w") as stream:
                stats = pstats.Stats(self.profiler, stream=stream)
                stats.sort_stats("cumulative").print_stats(60)
                stats.sort_stats("tottime").print_stats(60)
        lines = [
            "# Navigation 分段计时", "",
            f"完成：{complete}；总墙钟：{elapsed:.3f}s；步数：{dict(self.steps)}。", "",
            "--navigation-steps 限制采样步数时会删除未完成轨迹，只保留诊断结果。",
            "total 是包含子调用的耗时，self 扣除了已计时的子调用；嵌套 total 不能相加。",
            "按 phase 区分初始化、收臂、导航、恢复关节、清理和可选的视频回放。",
            "计时使用主机墙钟，不额外同步 CUDA；GPU 异步等待可能计入后续调用。",
            "steps.jsonl 分别记录每步 collection_step（含仿真和状态录制）与其后的 world_guard。",
            "控制计算、路况检查和碰撞检查由对应计时项及父项 self 覆盖。",
            "stacks.log 每 30 秒保留一次堆栈，可检查长时间不返回的调用。",
            "计时包装有额外开销；--cpu-profile 会进一步增加 Python 调用开销。", "",
            "| 阶段 / 调用 | 次数 | total s | self s | 平均 ms | 最大 ms |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
        for name, row in sorted(self.totals.items(), key=lambda item: -item[1]["self_s"]):
            lines.append(
                f"| {name} | {row['calls']} | {row['total_s']:.3f} | {row['self_s']:.3f} | "
                f"{row['total_s'] / row['calls'] * 1000:.3f} | {row['max_s'] * 1000:.3f} |"
            )
        if self.error:
            lines.extend(["", "```text", self.error, "```"])
        (self.output / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--from-hdf5", type=Path, default=FROM_HDF5)
    parser.add_argument("--to-hdf5", type=Path, default=TO_HDF5)
    parser.add_argument("--from-segment", default=FROM_SEGMENT)
    parser.add_argument("--to-segment", default=TO_SEGMENT)
    parser.add_argument("--from-demo", type=int, default=FROM_DEMO)
    parser.add_argument("--to-demo", type=int, default=TO_DEMO)
    parser.add_argument("--gpu", type=int, default=GPU)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--render", action="store_true", help="Also time replay and write navigation.mp4")
    parser.add_argument("--cpu-profile", action="store_true", help="Also save cProfile call statistics")
    parser.add_argument("--navigation-steps", type=int, default=0, help="Stop after N navigation steps; 0 runs the full bridge")
    args = parser.parse_args()
    if args.navigation_steps < 0:
        parser.error("--navigation-steps must be nonnegative")
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = {name: str(value) if isinstance(value, Path) else value for name, value in vars(args).items()}
    settings["python"] = sys.executable
    timer = NavigationTiming(output, settings)
    og = None

    os.environ["OMNIGIBSON_HEADLESS"] = "1"
    os.environ["OMNIGIBSON_GPU_ID"] = str(args.gpu)

    stack_log = (output / "stacks.log").open("w")
    faulthandler.dump_traceback_later(30, repeat=True, file=stack_log)
    try:
        from memory_bench.session import Session
        from memory_bench.utils.recordings import FrameReader, inspect_source, recording_metadata
        from memory_bench.utils.scene_state import merge_scene_assets, write_json
        from memory_bench.utils.segment_manifest import compatible_interfaces, mapped_snapshot

        with timer.measure("inspect_sources", phase="inputs"):
            session = Session.load(args.config)
            source = inspect_source(
                args.from_hdf5, session.segments[args.from_segment], session.scene_model, demo=args.from_demo,
            )
            target = inspect_source(
                args.to_hdf5, session.segments[args.to_segment], session.scene_model, demo=args.to_demo,
            )
            if not source["has_terminal_state"]:
                raise ValueError("The start recording must contain a real post-action terminal state")
            if not compatible_interfaces(source["interface"], target["interface"]):
                raise ValueError("The recordings have incompatible robot interfaces")
            if set(source["used"]) & set(target["used"]):
                raise ValueError("The two tasks use overlapping assets")
            print(f"Start: {args.from_hdf5}/demo_{args.from_demo} exit", flush=True)
            print(f"Target: {args.to_hdf5}/demo_{args.to_demo} entry", flush=True)

        import torch

        if not torch.cuda.is_available():
            raise RuntimeError("This navigation test requires CUDA and Isaac Sim in the behavior environment")
        torch.cuda.set_device(args.gpu)
        os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)

        with timer.measure("imports", phase="omnigibson_import"):
            import omnigibson as og
            from omnigibson.macros import gm
            from memory_bench.episodes.generate_bridges import generate_bridge

        with gm.unlocked():
            gm.RENDER_VIEWER_CAMERA = False

        session.paths = replace(session.paths, output_root=output)

        with timer.measure("load_states", phase="inputs"):
            print("Loading the start and target states", flush=True)
            with FrameReader(args.from_hdf5, args.from_demo) as reader:
                robot_name = source["interface"]["robot_name"]
                reader.metadata["scene"]["objects_info"]["init_info"][robot_name]["args"]["obs_modalities"] = []
                previous_exit = reader.snapshot(source["frames"][1])
            with FrameReader(args.to_hdf5, args.to_demo) as reader:
                robot_name = target["interface"]["robot_name"]
                reader.metadata["scene"]["objects_info"]["init_info"][robot_name]["args"]["obs_modalities"] = []
                next_entry = reader.snapshot(0)
            next_entry = mapped_snapshot(next_entry, target["interface"]["robot_name"], previous_exit["robot"]["name"])
            world = merge_scene_assets(previous_exit, next_entry, target["used"])

        timer.install()
        if timer.profiler is not None:
            timer.profiler.enable()

        print("Stow arms -> navigate -> restore joints", flush=True)
        trajectory = output / "bridge.hdf5"
        with timer.measure("generate_bridge", phase="bridge"):
            timer.manifest = generate_bridge(session, deepcopy(world), deepcopy(world), next_entry, trajectory)
        write_json(output / "navigation_report.json", timer.manifest)

        if args.render:
            from memory_bench.tools.replay_data import render_recording

            with timer.measure("render_recording", phase="video"):
                metadata = recording_metadata(trajectory)
                render_recording(
                    trajectory, metadata, output / "navigation.mp4", resolution=RESOLUTION,
                    n_render_iterations=int(session.export["n_render_iterations"]),
                    start_frame=0, end_frame=metadata["frames"],
                )
    except NavigationSampleComplete:
        timer.sample_complete = True
        print(f"TIMING completed {args.navigation_steps} navigation sample steps; partial bridge removed", flush=True)
    except BaseException:
        timer.error = traceback.format_exc()
        raise
    finally:
        if timer.profiler is not None:
            timer.profiler.disable()
        try:
            timer.save(complete=timer.error is None and not timer.sample_complete)
        finally:
            for owner, name, owned, original in reversed(timer.restore):
                if owned:
                    setattr(owner, name, original)
                else:
                    delattr(owner, name)
            print(f"Timing report: {output / 'report.md'}", flush=True)
            faulthandler.cancel_dump_traceback_later()
            stack_log.close()
            if og is not None and og.app is not None:
                og.shutdown()


if __name__ == "__main__":
    main()
