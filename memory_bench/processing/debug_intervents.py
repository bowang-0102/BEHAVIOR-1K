"""Run one real I/Bridge/I sequence without memory-task instances or K/Q recordings."""

import argparse
from copy import deepcopy
from functools import partial
import json
from pathlib import Path
import sys
import traceback

import yaml

from memory_bench.processing.compose_episodes import boundary, connect_segments, load_segments, write_sequence
from memory_bench.processing.prepare_intervents import prepare_intervent
from memory_bench.processing.recordings import inspect_source, recording_metadata
from memory_bench.processing.render_episodes import readback, render_episode, validate_annotations
from memory_bench.session import Session
from memory_bench.utils.paths import SessionPaths
from memory_bench.utils.state import (
    file_hash,
    merge_scene_assets,
    read_json,
    require_objects_match,
    robot_metadata,
    scene_objects,
    write_json,
)


EPISODE_ID = "episode_00000000"
WORLD_SETTLING_MAX_STEPS = 300
WORLD_SETTLING_QUIET_STEPS = 30


def prepare_sources(session):
    assets = {"mode": "intervent_debug", "protected": [], "intervent_objects": {}}
    inspected = {}
    occupied = set()
    for key in session.config["task_order"]:
        source = session.config["recordings"][key]
        info = inspect_source(
            source["hdf5"],
            session.segments[key],
            session.config["scene_model"],
            session.segments[key].get("extra_objects", []),
            source["demo"],
        )
        used = set(info["used"])
        if used & occupied:
            raise ValueError(f"Intervention assets overlap: {key}/{sorted(used & occupied)}")
        occupied.update(used)
        assets["intervent_objects"][key] = info["used"]
        inspected[key] = info
    asset_path = session.paths.resolve(session.config["assets"]["manifest"])
    if asset_path.exists():
        if read_json(asset_path) != assets:
            raise ValueError("Frozen debug assets changed; use a new output root")
    else:
        write_json(asset_path, assets)
    references = {}
    for key in session.config["task_order"]:
        source = session.config["recordings"][key]
        directory = session.paths.output_root / "segments" / session.session_id / key
        manifest = directory / "segment.json"
        if not manifest.exists():
            print(f"Preparing {key}: {source['hdf5']}", flush=True)
            manifest = prepare_intervent(session, key, source["hdf5"], source["demo"], directory, resume=True)
        references[key] = session.paths.relative(manifest)
    load_segments(session, references)
    for relative, value in (
        (f"sessions/{session.session_id}/intervent_segments.json", references),
        (f"reports/{session.session_id}/source_inventory.json", inspected),
    ):
        path = session.paths.resolve(relative)
        if path.exists():
            if read_json(path) != value:
                raise ValueError(f"Prepared source inventory changed: {path}")
        else:
            write_json(path, value)


def prepare_world(session, references):
    world_path = session.paths.resolve(f"sessions/{session.session_id}/world.json")
    report_path = session.paths.resolve(f"reports/{session.session_id}/world.json")
    if world_path.exists() or report_path.exists():
        raise FileExistsError("Prepared debug world already exists; continue with the next unfinished stage")

    import omnigibson as og
    from omnigibson.envs.data_wrapper import _add_recorded_non_kin_states_to_scene_file
    from omnigibson.macros import gm
    from omnigibson.object_states import Open
    from omnigibson.utils.config_utils import TorchEncoder

    from memory_bench.utils.state import state_matches

    order = session.config["task_order"]
    segments = load_segments(session, references)
    first = segments[order[0]]
    robot_name = first["interface"]["robot_name"]
    scene = read_json(session.config["full_scene"])
    for key in order:
        entry = boundary(session, segments[key], "entry", robot_name)
        scene = merge_scene_assets(scene, entry, segments[key]["objects"]["used"])
    entry = boundary(session, first, "entry", robot_name)
    scene = merge_scene_assets(scene, entry, [robot_name])
    scene["robot"] = deepcopy(entry["robot"])
    _add_recorded_non_kin_states_to_scene_file(scene, [scene])
    source = first["source"]
    config = deepcopy(recording_metadata(session.paths.resolve(source["hdf5"]), source["demo"])["config"])
    config["scene"].update(scene_file=scene, load_room_types=None, load_room_instances=None, include_robots=True)
    config["objects"] = []
    config["task"] = {"type": "DummyTask", "include_obs": False}
    config["env"].update(automatic_reset=False, external_sensors=None)
    if og.sim is not None:
        config.setdefault("render", {}).update(viewer_width=og.sim.viewer_width, viewer_height=og.sim.viewer_height)
    for robot in config["robots"]:
        robot["obs_modalities"] = []
    scene["objects_info"]["init_info"][robot_name]["args"]["obs_modalities"] = []
    with gm.unlocked():
        gm.ENABLE_TRANSITION_RULES = False
        gm.VISUAL_ONLY = False
    env = og.Environment(configs=config)
    used = set().union(*(set(item["objects"]["used"]) for item in segments.values()))
    doors = session.config["open_doorways"]
    if used & set(doors):
        raise ValueError("A configured passage door is used by an intervention")
    for name in doors:
        door = env.scene.object_registry("name", name)
        if not door.states[Open].set_value(True, fully=True):
            raise ValueError(f"Could not open passage door {name}")
    tolerance = session.config["validation"]["state_tolerances"]
    boundary_limits = session.config["validation"]["robot_boundary"]
    velocity_limits = {
        "linear_velocity": boundary_limits["linear_velocity"],
        "angular_velocity": boundary_limits["angular_velocity"],
        "joint_velocity": boundary_limits["joint_velocity"],
    }
    initial = json.loads(json.dumps(env.scene.save(as_dict=True), cls=TorchEncoder))
    baseline = {name: state for name, state in scene_objects(initial).items() if name != robot_name}
    quiet_steps = 0
    peak_velocities = {name: 0.0 for name in velocity_limits}
    last_reset = {}
    for step in range(1, WORLD_SETTLING_MAX_STEPS + 1):
        og.sim.step()
        merged = json.loads(json.dumps(env.scene.save(as_dict=True), cls=TorchEncoder))
        current = {name: state for name, state in scene_objects(merged).items() if name != robot_name}
        changed = [
            name
            for name in sorted(set(baseline) | set(current))
            if name not in baseline
            or name not in current
            or not state_matches(current[name], baseline[name], tolerance)
        ]
        velocities = {}
        for name, state in current.items():
            root = state.get("root_link", {})
            velocities[name] = {
                "linear_velocity": sum(value * value for value in root.get("lin_vel", [])) ** 0.5,
                "angular_velocity": sum(value * value for value in root.get("ang_vel", [])) ** 0.5,
                "joint_velocity": max((abs(value) for value in state.get("joint_vel", [])), default=0.0),
            }
        max_velocities = {field: max(values[field] for values in velocities.values()) for field in velocity_limits}
        moving = {
            name: values
            for name, values in velocities.items()
            if any(values[field] > velocity_limits[field] for field in velocity_limits)
        }
        for field in velocity_limits:
            peak_velocities[field] = max(peak_velocities[field], max_velocities[field])
        if changed or moving:
            quiet_steps = 0
            baseline = current
            last_reset = {"step": step, "changed_objects": changed, "moving_objects": moving}
        else:
            quiet_steps += 1
        if step % 30 == 0 or quiet_steps >= WORLD_SETTLING_QUIET_STEPS:
            print(
                f"Settling step {step}: quiet={quiet_steps}, changed={changed}, moving={sorted(moving)}",
                flush=True,
            )
        if quiet_steps >= WORLD_SETTLING_QUIET_STEPS:
            break
    settling = {
        "steps": step,
        "max_steps": WORLD_SETTLING_MAX_STEPS,
        "quiet_steps": quiet_steps,
        "quiet_target": WORLD_SETTLING_QUIET_STEPS,
        "velocity_limits": velocity_limits,
        "final_max_velocities": max_velocities,
        "peak_velocities": peak_velocities,
        "last_reset": last_reset,
        "passed": quiet_steps >= WORLD_SETTLING_QUIET_STEPS,
    }
    if not settling["passed"]:
        raise ValueError(f"Full scene did not physically settle: {json.dumps(settling)}")
    for name in doors:
        if not env.scene.object_registry("name", name).states[Open].get_value():
            raise ValueError(f"Passage door did not stay open: {name}")
    merged["robot"] = robot_metadata(env.scene.robots[0])
    for key in order:
        expected = boundary(session, segments[key], "entry", robot_name)
        require_objects_match(merged, expected, segments[key]["objects"]["read"], tolerance, f"{key} full-scene entry")
    # Use the recorded pre-action robot state; opening passage doors only prepares the world.
    scene_objects(merged)[robot_name] = deepcopy(scene_objects(entry)[robot_name])
    merged["robot"] = deepcopy(entry["robot"])
    write_json(world_path, merged)
    write_json(
        report_path,
        {
            "mode": "intervent_debug",
            "full_scene": session.config["full_scene"],
            "full_scene_sha256": file_hash(session.config["full_scene"]),
            "objects": len(scene_objects(merged)),
            "opened_doorways": doors,
            "world_sha256": file_hash(world_path),
            "settling": settling,
        },
    )


def build_bridge(session, references, env=None, resume_attempt=None):
    from memory_bench.processing.synthesize_bridges import synthesize_bridge

    segments = load_segments(session, references)
    scene_path = session.paths.resolve(f"sessions/{session.session_id}/world.json")
    scene = read_json(scene_path)
    report = read_json(session.paths.resolve(f"reports/{session.session_id}/world.json"))
    if file_hash(scene_path) != report["world_sha256"]:
        raise ValueError("Prepared debug world changed")
    physical = read_json(session.paths.resolve(f"reports/{session.session_id}/world_audit.json"))
    if not physical["passed"] or physical["world"]["sha256"] != file_hash(scene_path):
        raise ValueError("The prepared world needs a passed representative physical audit")
    for key, path in references.items():
        if physical["sources"][key]["manifest_sha256"] != file_hash(session.paths.resolve(path)):
            raise ValueError(f"Segment changed after the full-scene physical audit: {key}")
    # The standalone CLI shuts down on failure; avoid full-scene teardown before reporting it.
    assembled, bridges = connect_segments(
        session,
        scene,
        session.config["task_order"],
        segments,
        EPISODE_ID,
        bridge_builder=partial(synthesize_bridge, clear_on_failure=False, env=env, resume_attempt=resume_attempt),
    )
    all_references = {**references, **bridges}
    write_json(
        session.paths.resolve(f"sessions/{session.session_id}/assembly.json"),
        {
            "mode": "intervent_debug",
            "scene": session.paths.relative(scene_path),
            "scene_sha256": file_hash(scene_path),
            "references": all_references,
            "order": [item["identity"]["id"] for item in assembled],
        },
    )


def compose(session):
    assembly = read_json(session.paths.resolve(f"sessions/{session.session_id}/assembly.json"))
    scene_path = session.paths.resolve(assembly["scene"])
    if file_hash(scene_path) != assembly["scene_sha256"]:
        raise ValueError("Prepared debug world changed")
    segments = load_segments(session, assembly["references"])
    recipe = write_sequence(
        session,
        read_json(scene_path),
        [segments[key] for key in assembly["order"]],
        assembly["references"],
        set(),
        EPISODE_ID,
        {},
    )
    recipe["mode"] = "intervent_debug"
    validate_annotations(recipe)
    write_json(session.paths.resolve(f"recipes/{session.session_id}/{EPISODE_ID}.json"), recipe)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("stage", choices=["prepare", "scene", "audit", "bridge", "compose", "render", "readback"])
    parser.add_argument("--resume-attempt", help="Explicit archived terminal-stability failure to continue physically")
    parser.add_argument("--rgb-only", action="store_true", help="Preview RGB without rendering or exporting depth")
    args = parser.parse_args()
    if args.resume_attempt and args.stage != "bridge":
        parser.error("--resume-attempt is only available for bridge")
    if args.rgb_only and args.stage != "render":
        parser.error("--rgb-only is only available for render")
    config = yaml.safe_load(Path(args.config).read_text())
    if config["mode"] != "intervent_debug":
        raise ValueError("This entry is only for an explicitly marked intervention debug configuration")
    order = config["task_order"]
    if len(order) != 2 or set(order) != set(config["segments"]):
        raise ValueError("Provide exactly two distinct I tasks for one debug sequence")
    if any(item["role"] != "intervent_task" or item["depends_on"] for item in config["segments"].values()):
        raise ValueError("The debug sequence only accepts independent intervention tasks")
    rooms = [config["segments"][key]["room_id"] for key in order]
    if len(set(rooms)) != len(rooms):
        raise ValueError("Intervention tasks require distinct room instances")
    # Reuse the path/runtime container; formal Session.load/validate remains K/Q-only.
    session = Session(config, SessionPaths.from_config(config["paths"]))
    session.paths.activate()
    snapshot = session.paths.resolve(f"sessions/{session.session_id}/session.json")
    if snapshot.exists():
        if read_json(snapshot) != config:
            raise ValueError("Debug configuration changed; use a new output root")
    else:
        if args.stage != "prepare":
            raise ValueError("Run prepare before the remaining stages")
        write_json(snapshot, config)
    print(f"Running debug stage: {args.stage}", flush=True)
    try:
        if args.stage == "prepare":
            prepare_sources(session)
        elif args.stage in ("scene", "audit", "bridge"):
            references = read_json(session.paths.resolve(f"sessions/{session.session_id}/intervent_segments.json"))
            if args.stage == "scene":
                prepare_world(session, references)
            elif args.stage == "audit":
                from memory_bench.processing.audit_world import audit_world

                report = audit_world(session, references, f"sessions/{session.session_id}/world.json")
                if not report["passed"]:
                    attempts = session.paths.resolve(f"reports/{session.session_id}/world_audit_attempts")
                    attempts.mkdir(parents=True, exist_ok=True)
                    failure = attempts / f"attempt_{len(list(attempts.glob('attempt_*.json'))):04d}.json"
                    write_json(failure, report)
                    raise ValueError(f"Merged-world representative actions failed; inspect {failure}")
                write_json(session.paths.resolve(f"reports/{session.session_id}/world_audit.json"), report)
            else:
                build_bridge(session, references, resume_attempt=args.resume_attempt)
        elif args.stage == "compose":
            compose(session)
        else:
            recipe = read_json(session.paths.resolve(f"recipes/{session.session_id}/{EPISODE_ID}.json"))
            load_segments(session, {**recipe["segments"], **recipe["bridges"]})
            if args.stage == "render":
                dataset = session.paths.resolve(f"lerobot/{session.session_id}")
                if dataset.exists():
                    raise FileExistsError(f"Refusing to overwrite an existing export: {dataset}")
                render_episode(session, recipe, rgb_only=args.rgb_only)
            else:
                print(json.dumps(readback(session, [recipe]), indent=2))
        print(f"Completed debug stage: {args.stage}", flush=True)
    except Exception:
        report = session.paths.resolve(f"reports/{session.session_id}/{args.stage}_failure.json")
        if report.exists():
            attempt = len(list(report.parent.glob(f"{args.stage}_failure*.json")))
            report = report.with_name(f"{args.stage}_failure_{attempt:04d}.json")
        write_json(report, {"stage": args.stage, "traceback": traceback.format_exc()})
        traceback.print_exc()
        if "omnigibson" in sys.modules and sys.modules["omnigibson"].app is not None:
            # Kit's fast shutdown exits the process; preserve the stage's failure status.
            sys.modules["omnigibson"].app._app.post_quit(1)
        raise
    finally:
        if "omnigibson" in sys.modules and sys.modules["omnigibson"].app is not None:
            sys.modules["omnigibson"].shutdown()


if __name__ == "__main__":
    main()
