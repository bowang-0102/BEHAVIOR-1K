"""Audit representative real task actions in the inherited, merged physical world."""

import json
from copy import deepcopy

from memory_bench.episodes.synthesize_bridges import _build_config
from memory_bench.utils.recordings import FrameReader
from memory_bench.utils.scene_state import (
    get_state_tolerances,
    read_json,
    scene_objects,
    scene_systems,
    state_matches,
)
from memory_bench.utils.segment_manifest import boundary, load_segments, mapped_snapshot


def audit_world(session, references, world_path):
    """Return representative-point evidence, not a proof of full-trajectory feasibility."""
    import torch as th
    import omnigibson as og
    from omnigibson.macros import gm
    from omnigibson.utils.config_utils import TorchEncoder
    from omnigibson.utils.motion_planning_utils import detect_robot_collision_in_sim
    from omnigibson.utils.python_utils import recursively_convert_to_torch
    from omnigibson.utils.registry_utils import m as registry_macros
    from omnigibson.utils.usd_utils import RigidContactAPI

    segments = load_segments(session, references)
    order = session.task_order
    world_path = session.paths.resolve(world_path)
    world = read_json(world_path)
    robot_name = world["robot"]["name"]
    tolerance = get_state_tolerances()
    report = {
        "scope": "representative_frames",
        "passed": True,
        "world": {"path": session.paths.relative(world_path)},
        "task_order": order,
        "sources": {
            key: {
                "source": deepcopy(segments[key]["source"]),
            }
            for key in order
        },
        "tasks": [],
    }
    samples_by_key, states_by_key, actions_by_key = {}, {}, {}
    for key, segment in segments.items():
        source = segment["source"]
        start, end = source["frames"]
        length = end - start
        samples = [start, start + length // 4, start + length // 2, start + 3 * length // 4, end - 1]
        samples_by_key[key] = samples
        with FrameReader(session.paths.resolve(source["hdf5"]), source["demo"]) as reader:
            states_by_key[key] = {frame: reader.state(frame) for frame in samples}
            actions_by_key[key] = {frame: reader.group["action"][frame] for frame in samples}
            segment["boundaries"] = {"entry": reader.snapshot(start), "exit": reader.snapshot(end)}
    with gm.unlocked():
        gm.VISUAL_ONLY = False
        gm.ENABLE_TRANSITION_RULES = False

    interface = segments[order[0]]["interface"]
    env = og.Environment(configs=_build_config(session, interface["action_frequency"], world))
    env.scene.restore(world, update_initial_file=True)
    if registry_macros.STABILIZE_SKIPPED_OBJECTS:
        raise RuntimeError("Physical audit must not stabilize skipped background objects")
    report["stabilize_skipped_objects"] = False
    report["background_state_policy"] = "inherit_live_state"
    robot = env.scene.robots[0]
    link_owners = {link.prim_path: obj.name for obj in env.scene.objects for link in obj.links.values()}
    baseline_objects = None
    baseline_systems = None
    for key in order:
        segment = segments[key]
        if segment["task"]["role"] != "intervent_task":
            raise ValueError(f"Representative world audit expects an I source: {key}")
        used = set(segment["objects"]["used"])
        source_scene = boundary(session, segment, "entry", robot_name)
        samples = samples_by_key[key]
        task_report = {"id": key, "passed": True, "samples": []}
        report["tasks"].append(task_report)
        print(f"Auditing {key}: source frames {samples}", flush=True)
        for frame in samples:
            incoming = mapped_snapshot(states_by_key[key][frame], segment["interface"]["robot_name"], robot_name)
            state = deepcopy(world["state"])
            # Sampling must preserve the live background, as composition does.
            # Reloading every background body wakes contacts unrelated to this task.
            scene_objects(state).clear()
            for name in used | {robot_name}:
                scene_objects(state)[name] = deepcopy(scene_objects(incoming)[name])
            env.scene.load_state(recursively_convert_to_torch(state), serialized=False)
            first_sample = baseline_objects is None
            if first_sample:
                restored = json.loads(json.dumps(env.scene.save(as_dict=True), cls=TorchEncoder))
            # Loading poses leaves derived bounds cached; refresh without advancing physics.
            og.sim._refresh_state_caches(dt=0.0)
            before = json.loads(json.dumps(env.scene.save(as_dict=True), cls=TorchEncoder))
            if first_sample:
                baseline_objects = deepcopy(scene_objects(before))
                baseline_systems = deepcopy(scene_systems(before))
                loaded_objects, fresh_objects = scene_objects(restored), scene_objects(before)
                report["initial_cache_refresh"] = {
                    name: {"before": loaded_objects[name], "after": fresh_objects[name]}
                    for name in sorted(set(loaded_objects) - used - {robot_name})
                    if not state_matches(loaded_objects[name], fresh_objects[name], tolerance)
                }
            env.step(th.as_tensor(actions_by_key[key][frame], dtype=th.float32))
            collision = detect_robot_collision_in_sim(
                robot,
                filter_objs=[env.scene.object_registry("name", name) for name in sorted(used)],
                ignore_obj_in_hand=False,
            )
            after = json.loads(json.dumps(env.scene.save(as_dict=True), cls=TorchEncoder))
            before_objects, after_objects = scene_objects(before), scene_objects(after)
            background = set(before_objects) - used - {robot_name}
            source_objects = scene_objects(incoming)
            # Unchanged source geometry permits the original floor/support contacts.
            # New or moved background objects must not contact manipulated objects.
            altered_background = []
            for name in sorted(background):
                source_info = source_scene["objects_info"]["init_info"].get(name, {}).get("args", {})
                world_info = before["objects_info"]["init_info"][name]["args"]
                same_asset = all(
                    source_info.get(field) == world_info.get(field)
                    for field in ("category", "model", "scale", "fixed_base")
                )
                if (
                    name not in source_objects
                    or not same_asset
                    or not state_matches(before_objects[name], source_objects[name], tolerance)
                ):
                    altered_background.append(env.scene.object_registry("name", name))
            contacts = RigidContactAPI.get_contact_pairs(
                scene_idx=env.scene.idx,
                query_set=[env.scene.object_registry("name", name) for name in sorted(segment["objects"]["write"])],
                with_set=altered_background,
                current_only=False,
            )
            object_conflicts = sorted({(link_owners[a], link_owners[b]) for a, b in contacts})
            step_changed = [
                name
                for name in sorted(background)
                if name not in after_objects
                or not state_matches(after_objects[name], before_objects[name], tolerance)
            ]
            cumulative_changed = [
                name
                for name in sorted(background)
                if name not in after_objects
                or name not in baseline_objects
                or not state_matches(after_objects[name], baseline_objects[name], tolerance)
            ]
            changed = sorted(set(step_changed) | set(cumulative_changed))
            topology_preserved = set(before_objects) == set(after_objects) == set(baseline_objects)
            systems_preserved = state_matches(
                scene_systems(after), scene_systems(before), tolerance
            ) and state_matches(scene_systems(after), baseline_systems, tolerance)
            passed = (
                not collision and not object_conflicts and not changed and topology_preserved and systems_preserved
            )
            task_report["samples"].append(
                {
                    "source_frame": frame,
                    "robot_collision_free": not collision,
                    "object_background_collision_free": not object_conflicts,
                    "new_background_contacts": object_conflicts,
                    "background_state_preserved": not changed and topology_preserved,
                    "changed_background_objects": changed,
                    "step_changed_objects": step_changed,
                    "cumulative_changed_objects": cumulative_changed,
                    "background_differences": {
                        name: {
                            "baseline": baseline_objects.get(name),
                            "before": before_objects[name],
                            "after": after_objects.get(name),
                        }
                        for name in changed
                    },
                    "topology_preserved": topology_preserved,
                    "systems_preserved": systems_preserved,
                    "passed": passed,
                }
            )
            task_report["passed"] &= passed
            report["passed"] &= passed
            print(f"Audited {key} frame {frame}: passed={passed}", flush=True)
        exit_state = boundary(session, segment, "exit", robot_name)
        inherited = set(segment["objects"]["write"]) | {robot_name}
        handoff = deepcopy(world["state"])
        scene_objects(handoff).clear()
        for name in inherited:
            scene_objects(world)[name] = deepcopy(scene_objects(exit_state)[name])
            scene_objects(handoff)[name] = deepcopy(scene_objects(exit_state)[name])
        env.scene.load_state(recursively_convert_to_torch(handoff), serialized=False)
        og.sim._refresh_state_caches(dt=0.0)
        actual_exit = json.loads(json.dumps(env.scene.save(as_dict=True), cls=TorchEncoder))
        for name in segment["objects"]["write"]:
            baseline_objects[name] = deepcopy(scene_objects(actual_exit)[name])
        world["robot"] = deepcopy(exit_state["robot"])
    return report
