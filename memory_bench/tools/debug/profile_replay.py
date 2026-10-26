"""Time the real Memory Bench replay without editing production files.

Run from the repository root in the behavior environment, for example:
    python memory_bench/tools/debug/profile_replay.py EPISODE.hdf5 --config SESSION.yaml --output-dir /tmp/replay_timing
"""

import argparse
from collections import Counter, defaultdict
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time
import traceback

import h5py

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from memory_bench.session import Session
from memory_bench.tools import replay_data
from memory_bench.utils.recordings import recording_metadata


class ReplayTiming:
    def __init__(self, output, start_frame, warmup_frames, baseline=False, save_states=False):
        self.output = output
        self.start_frame = start_frame
        self.warmup_frames = warmup_frames
        self.baseline = baseline
        self.save_states = save_states
        self.rows = []
        self.current = None
        self.phase = "outside_helper"
        self.previous_state = None
        self.restore = []
        self.main_thread = threading.get_ident()
        self.installed = False
        self.last_trace_time = None
        self.scene_info = {}

    def patch(self, owner, name, replacement):
        original = getattr(owner, name)
        self.restore.append((owner, name, original))
        setattr(owner, name, replacement)
        return original

    def add(self, name, elapsed, nested=False):
        row = self.current
        if row is not None and threading.get_ident() == self.main_thread:
            bucket = row["nested_s" if nested else "stages_s"]
            bucket[name] += elapsed
            if nested:
                row["nested_calls"][name] += 1
                row["nested_by_phase_s"][f"{self.phase}/{name}"] += elapsed

    def timed(self, function, name, nested=False):
        @wraps(function)
        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                self.add(name, time.perf_counter() - started, nested)

        return wrapped

    def trace(self, frame, event, arg):
        if frame.f_code is not self.helper_code:
            return None
        if event in ("line", "return"):
            now = time.perf_counter()
            if self.last_trace_time is not None:
                self.add(self.phase, now - self.last_trace_time)
            if event == "line":
                self.phase = self.line_stages[frame.f_lineno]
                self.last_trace_time = time.perf_counter()
            else:
                self.last_trace_time = None
        return self.trace

    def install_runtime_timers(self, env, helper):
        # Called from the first helper invocation: Kit has already launched by then.
        import omnigibson as og

        self.helper_code = helper.__code__
        self.env = env
        source, first_line = inspect.getsourcelines(helper)
        anchors = {
            "state = recursively_convert_to_torch": "tensor_conversion",
            "object_registry = env.scene.object_registry": "scene_load_state",
            "env.scene.load_state": "scene_load_state",
            'registry = state["registry"]': "pose_rebase",
            "for name, object_state in": "second_kinematic_restore",
            "og.sim.physics_sim_view.update_articulations_kinematic": "articulation_update",
            "og.sim._sim_context._physx_fabric_interface.force_update": "fabric_force_update",
            "og.sim._refresh_state_caches": "state_cache_refresh",
            "for obj in env.scene.objects": "update_visuals",
            "og.sim.fabric_hierarchy.update_world_xforms": "camera_world_xforms",
            "proprio =": "proprioception",
            "for _ in range(n_render_iterations)": "render",
            "observation, obs_info = env.get_obs": "get_obs",
            "return observation": "helper_return",
        }
        self.line_stages = {}
        found = set()
        stage = "helper_setup"
        for offset, line in enumerate(source):
            for prefix, name in anchors.items():
                if line.strip().startswith(prefix):
                    stage = name
                    found.add(prefix)
                    break
            self.line_stages[first_line + offset] = stage
        if found != set(anchors):
            raise RuntimeError(f"Replay helper changed; missing timing anchors: {set(anchors) - found}")

        timer = self
        original_editing = og.sim.editing_usd

        class TimedEdit:
            def __init__(self, context):
                self.context = context

            def __enter__(self):
                started = time.perf_counter()
                try:
                    return self.context.__enter__()
                finally:
                    timer.add("usd_edit_enter", time.perf_counter() - started, nested=True)

            def __exit__(self, *exc):
                started = time.perf_counter()
                try:
                    return self.context.__exit__(*exc)
                finally:
                    # Includes the real SynchronizeToFabric and context-exit bookkeeping.
                    timer.add("usd_edit_exit_sync", time.perf_counter() - started, nested=True)

        self.patch(og.sim, "editing_usd", lambda *a, **kw: TimedEdit(original_editing(*a, **kw)))
        self.patch(
            og.sim, "sync_physx_to_fabric",
            self.timed(og.sim.sync_physx_to_fabric, "physx_to_fabric_sync", nested=True),
        )

        hierarchy = og.sim.fabric_hierarchy

        class TimedHierarchy:
            def update_world_xforms(self, *args, **kwargs):
                started = time.perf_counter()
                try:
                    return hierarchy.update_world_xforms(*args, **kwargs)
                finally:
                    timer.add("world_xforms_all_calls", time.perf_counter() - started, nested=True)

            def __getattr__(self, name):
                return getattr(hierarchy, name)

        self.patch(og.sim, "fabric_hierarchy", TimedHierarchy())
        self.scene_info = {
            "objects": len(env.scene.objects),
            "root_link_types": dict(Counter(type(obj.root_link).__name__ for obj in env.scene.objects)),
        }
        self.installed = True

    def install(self):
        from memory_bench.episodes import replay_episodes
        from omnigibson.eval.utils import obs_utils

        original_read = h5py.Dataset.__getitem__

        @wraps(original_read)
        def read(dataset, key):
            if dataset.name != "/data/demo_0/state_json":
                return original_read(dataset, key)
            self.current = {
                "frame": int(key),
                "warmup": int(key) < self.start_frame + self.warmup_frames,
                "started": time.perf_counter(),
                "stages_s": defaultdict(float),
                "nested_s": defaultdict(float),
                "nested_calls": defaultdict(int),
                "nested_by_phase_s": defaultdict(float),
            }
            started = time.perf_counter()
            raw = original_read(dataset, key)
            self.add("hdf5_read", time.perf_counter() - started)
            self.raw = raw
            return raw

        original_loads = json.loads

        @wraps(original_loads)
        def loads(value, *args, **kwargs):
            is_state = self.current is not None and value is self.raw
            started = time.perf_counter()
            result = original_loads(value, *args, **kwargs)
            if is_state:
                self.add("json_decode", time.perf_counter() - started)
                self.decoded = result
            return result

        helper = replay_episodes._render_observation

        @wraps(helper)
        def render_observation(env, state, iterations, *, load_names=None, repair_names=None):
            import omnigibson as og

            if not self.installed:
                self.install_runtime_timers(env, helper)
            if self.baseline:
                load_names = repair_names = None
            self.current["restored_objects"] = len(
                state["registry"]["object_registry"] if load_names is None else load_names
            )
            self.current["full_restore"] = load_names is None
            physics_time = og.sim.current_time
            started = time.perf_counter()
            previous_trace = sys.gettrace()
            if not self.current["warmup"]:
                self.phase = "helper_setup"
                self.last_trace_time = None
                sys.settrace(self.trace)
            try:
                return helper(env, state, iterations, load_names=load_names, repair_names=repair_names)
            finally:
                sys.settrace(previous_trace)
                self.current["helper_wall_s"] = time.perf_counter() - started
                self.current["physics_time"] = og.sim.current_time
                assert og.sim.current_time == physics_time, "Replay advanced physics"
                self.phase = "outside_helper"

        original_write = obs_utils.write_video

        @wraps(original_write)
        def write_video(*args, **kwargs):
            started = time.perf_counter()
            result = original_write(*args, **kwargs)
            self.add("encode_mux", time.perf_counter() - started)
            row = self.current
            row["wall_s"] = time.perf_counter() - row.pop("started")
            row["unattributed_s"] = row["wall_s"] - sum(row["stages_s"].values())
            objects = self.decoded["registry"]["object_registry"]
            row["changed_objects"] = (
                None
                if self.previous_state is None
                else sum(value != self.previous_state[name] for name, value in objects.items())
            )
            self.previous_state = objects
            self.rows.append(row)
            self.current = None
            if self.save_states:
                # Outside the measured frame: compare live kinematics, attachments and camera caches across runs.
                from omnigibson.utils.usd_utils import get_world_pose_with_scale

                sensors = dict(self.env.scene.robots[0].sensors)
                sensors.update(self.env.external_sensors[0])
                snapshot = {
                    "state": self.env.scene.dump_state(serialized=False),
                    "cameras": {name: get_world_pose_with_scale(sensor.prim_path) for name, sensor in sensors.items()},
                    "physics_time": row["physics_time"],
                }
                (self.output / f"state_{row['frame']:06d}.json").write_text(
                    json.dumps(snapshot, default=lambda value: value.tolist())
                )
            with (self.output / "frames.jsonl").open("a") as stream:
                stream.write(json.dumps(row) + "\n")
            print(f"TIMING frame={row['frame']} warmup={row['warmup']} wall={row['wall_s']:.3f}s", flush=True)
            return result

        self.patch(h5py.Dataset, "__getitem__", read)
        self.patch(json, "loads", loads)
        self.patch(replay_data, "deepcopy", self.timed(replay_data.deepcopy, "state_deepcopy"))
        self.patch(replay_data, "inspection_frame", self.timed(replay_data.inspection_frame, "image_compose"))
        self.patch(replay_episodes, "_render_observation", render_observation)
        self.patch(obs_utils, "write_video", write_video)

    def save(self, settings, elapsed, error):
        measured = [row for row in self.rows if not row["warmup"]]
        wall = sum(row["wall_s"] for row in measured)
        summary = {
            "settings": settings, "scene": self.scene_info, "run_wall_s": elapsed,
            "measured_frames": len(measured), "measured_wall_s": wall, "error": error,
            "frame_mean_ms": statistics.mean(row["wall_s"] for row in measured) * 1000 if measured else None,
            "frame_median_ms": statistics.median(row["wall_s"] for row in measured) * 1000 if measured else None,
            "stages": {}, "nested": {},
        }
        for bucket, destination in (("stages_s", "stages"), ("nested_s", "nested")):
            names = sorted({name for row in measured for name in row[bucket]})
            for name in names:
                values = [row[bucket].get(name, 0.0) for row in measured]
                total = sum(values)
                summary[destination][name] = {
                    "total_s": total, "mean_ms": statistics.mean(values) * 1000,
                    "median_ms": statistics.median(values) * 1000,
                    "wall_percent": 100 * total / wall if wall else 0,
                }
                if destination == "nested":
                    summary[destination][name]["calls"] = sum(row["nested_calls"].get(name, 0) for row in measured)
        summary["unattributed_s"] = sum(row["unattributed_s"] for row in measured)
        (self.output / "summary.json").write_text(json.dumps(summary, indent=2))
        lines = [
            "# Replay 分段计时", "",
            f"测量帧数：{len(measured)}；预热帧数：{self.warmup_frames}；总运行时间：{elapsed:.3f} 秒。",
            f"测量帧平均耗时：{wall / len(measured):.3f} 秒。" if measured else "尚未完成测量帧。",
            "", f"恢复模式：{'debug 强制完整恢复' if self.baseline else '自动筛选变化物体'}。源 HDF5 只读。",
            "使用主机墙钟，不额外调用 CUDA synchronize；异步 GPU 工作可能在后续等待点体现。",
            "逐行追踪只针对原始 _render_observation，且在 Kit 启动后启用；测量包装本身有开销。",
            "初始化、首帧额外渲染和预热不计入下表。run_wall_s 还包含加载和编码收尾。", "",
        ]
        if measured:
            largest = max(summary["stages"], key=lambda name: summary["stages"][name]["total_s"])
            restore_s = sum(
                summary["stages"][name]["total_s"]
                for name in ("scene_load_state", "second_kinematic_restore")
            )
            sync = summary["nested"]["usd_edit_exit_sync"]
            changes = [row["changed_objects"] for row in measured if row["changed_objects"] is not None]
            lines.extend([
                "## 测量结论", "",
                f"耗时最大的单阶段：{largest}。",
                f"两次状态恢复合计 {restore_s / len(measured) * 1000:.3f} ms/帧，"
                f"占整帧 {100 * restore_s / wall:.2f}%。",
                f"其中 USD 编辑退出及同步 {sync['mean_ms']:.3f} ms/帧，"
                f"占整帧 {sync['wall_percent']:.2f}%，每帧平均 {sync['calls'] / len(measured):.1f} 次。",
                "同步耗时已包含在状态恢复中；不能把这两个百分比相加，也不能把状态恢复的全部耗时归因于同步。",
                f"场景物体数：{self.scene_info['objects']}；根链接类型：{self.scene_info['root_link_types']}。",
                f"本次测量帧中，相邻帧变化物体数为 {min(changes)}～{max(changes)}。" if changes else "",
                "这是指定片段的主机墙钟测量，包含计时开销；渲染列包含提交和等待，不等于纯 GPU 核执行时间。",
                "",
                "本次状态恢复超过总耗时的一半，应优先检查未变化物体的重复恢复、第二次恢复范围和逐物体同步。"
                if restore_s > wall / 2 else f"本次应优先检查 {largest} 阶段。",
                f"测量帧中实际恢复物体数：{min(row['restored_objects'] for row in measured)}～"
                f"{max(row['restored_objects'] for row in measured)}。",
                f"整帧耗时中位数：{summary['frame_median_ms']:.3f} ms。", "",
            ])
        if error:
            lines.extend(["运行失败，以下结果可能不完整：", "```text", error, "```", ""])
        for key, title in (("stages", "各阶段（互不重叠）"), ("nested", "嵌套明细（已包含在上表中，不能相加）")):
            lines.extend([
                f"## {title}", "", "| 阶段 | 平均 ms/帧 | 中位数 ms/帧 | 占帧耗时 | 调用数 |",
                "| --- | ---: | ---: | ---: | ---: |",
            ])
            for name, value in sorted(summary[key].items(), key=lambda item: -item[1]["total_s"]):
                lines.append(
                    f"| {name} | {value['mean_ms']:.3f} | {value['median_ms']:.3f} | "
                    f"{value['wall_percent']:.2f}% | {value.get('calls', '—')} |"
                )
            lines.append("")
        lines.extend([
            f"未归属耗时：{summary['unattributed_s']:.6f} 秒。", "",
            "usd_edit_exit_sync 包含真实 SynchronizeToFabric 及少量上下文退出处理。",
            "world_xforms_all_calls 统计该帧所有层级更新，包含逐物体 setter 内部调用。",
            "physx_to_fabric_sync 统计重复 PhysX→Fabric 同步；单次 force_update 在主表单列。",
            "frames.jsonl 保留逐帧阶段耗时、嵌套调用次数、嵌套耗时所属阶段及变化物体数。", "",
            "```json", json.dumps(settings, indent=2), "```", "",
        ])
        (self.output / "report.md").write_text("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="New directory; never overwrite results")
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--warmup-frames", type=int, default=5)
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--resolution", type=int, default=240)
    parser.add_argument("--n-render-iterations", type=int, default=1)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--baseline", action="store_true", help="Debug only: force both restores to include every object")
    parser.add_argument("--save-states", action="store_true", help="Save live states/camera transforms outside frame timing")
    parser.add_argument("--check-tail", action="store_true", help="Also compare a fresh replay range ending at the terminal state")
    args = parser.parse_args()
    if (
        args.frames < 1 or args.warmup_frames < 1 or args.resolution <= 0
        or args.resolution % 2 or args.n_render_iterations < 1
    ):
        parser.error("Use positive frame counts/render iterations and a positive even resolution")
    path = args.input.expanduser().resolve()
    session = Session.load(args.config)
    metadata = recording_metadata(path)
    end = args.start_frame + args.warmup_frames + args.frames
    if not 0 <= args.start_frame < end <= metadata["frames"]:
        parser.error(f"Requested frame range exceeds {metadata['frames']} actions")
    with h5py.File(path, "r") as recording:
        if "state_json" not in recording["data/demo_0"]:
            parser.error("This diagnostic targets composed episodes with full state_json")
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=False)
    os.environ["OMNIGIBSON_DATA_PATH"] = str(session.paths.data_root)
    os.environ["OMNIGIBSON_HEADLESS"] = "1"
    os.environ["OMNIGIBSON_GPU_ID"] = str(args.gpu)
    settings = {name: str(value) if isinstance(value, Path) else value for name, value in vars(args).items()}
    settings["python"] = sys.executable
    settings["cpu_threads"] = {name: os.environ.get(name) for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS")}
    import omnigibson as og

    timer = ReplayTiming(output, args.start_frame, args.warmup_frames, args.baseline, args.save_states)
    started = time.perf_counter()
    error = None
    timer.install()
    try:
        replay_data.render_recording(
            path, metadata, output / "sample.mp4", resolution=args.resolution,
            n_render_iterations=args.n_render_iterations, start_frame=args.start_frame, end_frame=end,
        )
    except BaseException:
        error = traceback.format_exc()
        raise
    finally:
        elapsed = time.perf_counter() - started
        # Save and restore before Kit shutdown, which may exit the interpreter directly.
        timer.save(settings, elapsed, error)
        for owner, name, original in reversed(timer.restore):
            setattr(owner, name, original)
        print(f"Timing report: {output / 'report.md'}", flush=True)
        try:
            if args.check_tail and error is None:
                for baseline in (True, False):
                    tail_output = output / ("tail_full" if baseline else "tail_changed")
                    tail_output.mkdir()
                    tail_start = max(0, metadata["frames"] - 6)
                    tail_timer = ReplayTiming(tail_output, tail_start, 1, baseline, save_states=True)
                    tail_settings = dict(
                        settings, start_frame=tail_start, baseline=baseline, warmup_frames=1,
                        frames=metadata["frames"] - tail_start - 1, save_states=True, reused_environment=True,
                    )
                    tail_timer.install()
                    # Reuse loaded assets; render_recording starts with a fresh cache and a full first restore.
                    tail_timer.patch(og, "Environment", lambda **kwargs: timer.env)
                    tail_started = time.perf_counter()
                    tail_error = None
                    try:
                        replay_data.render_recording(
                            path, metadata, tail_output / "sample.mp4", resolution=args.resolution,
                            n_render_iterations=args.n_render_iterations, start_frame=tail_start,
                            end_frame=metadata["frames"],
                        )
                    except BaseException:
                        tail_error = traceback.format_exc()
                        raise
                    finally:
                        tail_timer.save(tail_settings, time.perf_counter() - tail_started, tail_error)
                        for owner, name, original in reversed(tail_timer.restore):
                            setattr(owner, name, original)
        finally:
            og.shutdown()


if __name__ == "__main__":
    main()
