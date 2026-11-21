import json
import sys
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import h5py
import numpy as np
import pytest
import torch as th

from memory_bench.episodes import build_episodes
from memory_bench.episodes.generate_bridges import _assert_world_preserved, _scene_snapshot, generate_bridge
from memory_bench.tests.helpers import make_scene_snapshot
from memory_bench.utils.scene.state import read_json, scene_objects, write_json


@pytest.mark.parametrize(
    "state_name,field", [("Open", "open"), ("OnFire", "on_fire"), ("HeatSourceOrSink", "heat_source_or_sink")]
)
def test_bridge_accepts_added_states_and_guards_them_during_motion(state_name, field):
    recorded = make_scene_snapshot({"robot": 0, "toy": 0})
    scene_objects(recorded)["toy"]["non_kin"] = {"Temperature": {"temperature": 23}}
    restored = deepcopy(recorded)
    scene_objects(restored)["toy"]["non_kin"][state_name] = {field: False}
    _assert_world_preserved(recorded, restored)
    _assert_world_preserved(restored, deepcopy(restored))

    moved = deepcopy(restored)
    non_kin = scene_objects(moved)["toy"]["non_kin"]
    non_kin["Temperature"]["temperature"] = 50
    _assert_world_preserved(restored, moved)
    del non_kin["Temperature"]
    _assert_world_preserved(restored, moved)

    non_kin[state_name][field] = True
    with pytest.raises(RuntimeError, match="nonrobot world state.*toy"):
        _assert_world_preserved(restored, moved)
    del non_kin[state_name]
    with pytest.raises(RuntimeError, match="nonrobot world state.*toy"):
        _assert_world_preserved(restored, moved)


@pytest.mark.parametrize("single_instance", [False, True])
def test_build_batch_continues_after_failure_and_skips_existing(episode_sources, monkeypatch, single_instance):
    session = episode_sources[0]
    assignments = session.paths.session_dir / "variant_assignments.json"
    values = read_json(assignments)
    values["instances"] = [{"instance_id": index} for index in range(1, 4)]
    assignments.write_text(json.dumps(values))
    existing = session.paths.recipes_dir / "episode_00000001.json"
    write_json(existing, {"completed": True})
    monkeypatch.setattr(build_episodes.Session, "load", lambda path: session)
    arguments = ["build_episodes", "config"]
    if single_instance:
        arguments += ["--instance-id", "1"]
    monkeypatch.setattr(sys, "argv", arguments)
    calls = []

    def build(session, instance_id, order):
        calls.append(instance_id)
        if instance_id == 1:
            raise RuntimeError("No traversable route")
        return f"instance {instance_id} built"

    monkeypatch.setattr(build_episodes, "build_episode", build)
    with pytest.raises(RuntimeError, match="No traversable route" if single_instance else "failed instances"):
        build_episodes.main()
    if single_instance:
        assert calls == [1]
        assert not session.paths.reports_dir.exists()
    else:
        assert calls == [1, 3]
        report = read_json(session.paths.reports_dir / "build_episodes.json")
        assert len(report["failed"]) == 1
        assert report["failed"][0]["instance_id"] == 1
        assert "No traversable route" in report["failed"][0]["error"]
    assert read_json(existing) == {"completed": True}


def test_bridge_guard_compares_saved_live_snapshots(monkeypatch):
    original = make_scene_snapshot({"robot": 0, "toy": 0})
    refresh = Mock()
    monkeypatch.setitem(
        sys.modules,
        "omnigibson.object_states.tensorized_state",
        SimpleNamespace(TensorizedState=SimpleNamespace(maybe_refresh_caches=refresh)),
    )
    monkeypatch.setattr(
        "memory_bench.episodes.generate_bridges.robot_metadata", lambda _: deepcopy(original["robot"])
    )
    monkeypatch.setitem(sys.modules, "omnigibson.utils.config_utils", SimpleNamespace(TorchEncoder=json.JSONEncoder))
    scene = SimpleNamespace(robots=[object()], save=Mock(return_value=original))
    entry = _scene_snapshot(scene)
    scene_objects(original)["toy"]["root_link"]["pos"][0] = 1
    live = _scene_snapshot(scene)
    assert set(scene_objects(live)) == set(scene_objects(entry))
    assert live["objects_info"] == entry["objects_info"]
    assert scene_objects(entry)["toy"]["root_link"]["pos"][0] == 0
    with pytest.raises(RuntimeError, match="toy"):
        _assert_world_preserved(entry, live)

    scene_objects(original)["toy"]["root_link"]["pos"][0] = 0
    original["state"]["registry"]["system_registry"]["water"] = {"particles": [[0, 0, 0]]}
    with pytest.raises(RuntimeError, match="topology"):
        _assert_world_preserved(entry, _scene_snapshot(scene))
    assert refresh.call_count == scene.save.call_count == 3


@pytest.mark.parametrize(
    "outcome,expected_actions,expected_error",
    [
        ("stow", 1, "original bridge failure"),
        ("navigation", 2, "original bridge failure"),
        ("restore", 3, "original bridge failure"),
        ("restore_contact", 3, "test arm contact"),
        ("blocked_retreat", 0, "test retreat blocked"),
        ("world_change", 3, "nonrobot world state"),
        ("broken_goal", 3, "broke outfit_a_basic_toolbox goal.*inside"),
        ("next_precondition", 3, "key_1 preconditions after Bridge.*toy"),
        ("allowed_motion", 3, None),
        ("transient_motion", 3, None),
        ("transient_joint_velocity", 3, None),
        ("terminal_joint_velocity", 3, "nonrobot world state.*exit"),
        (None, 3, None),
    ],
)
def test_bridge_synthesis_saves_success_and_cleans_failures(
    episode_sources, monkeypatch, capsys, outcome, expected_actions, expected_error,
):
    from memory_bench.utils.recording import wrappers

    session, _, _, segments = episode_sources
    previous = read_json(session.paths.output_root / "scene.json")
    previous["robot"].update(joint_positions=[0], controlled_joint_indices=[0])
    scene_objects(previous)["toy"]["joint_vel"] = [-0.00195]
    scene_objects(previous)["toy"]["non_kin"]["AABB"] = {"aabb": [0, 0, 0, 1, 1, 1]}
    live_objects = {
        name: SimpleNamespace(name=name, states={}, _recorded_non_kin_state_order=())
        for name in scene_objects(previous)
    }
    robot = SimpleNamespace(
        name="robot",
        model="r1pro",
        action_dim=23,
        _ag_obj_in_hand={"left": None},
        reset_joint_pos=th.zeros(1),
        trunk_control_idx=th.tensor([0]),
        arm_names=["left"],
        arm_control_idx={"left": th.tensor([0])},
        gripper_control_idx={"left": th.tensor([0])},
        get_joint_velocities=lambda: th.zeros(1),
        get_linear_velocity=lambda: th.zeros(3),
        get_angular_velocity=lambda: th.zeros(3),
    )
    scene = SimpleNamespace(
        robots=[robot],
        restore=Mock(),
        load_state=Mock(),
        save=Mock(return_value=previous),
        dump_state=Mock(return_value=previous["state"]),
        object_registry=lambda field, name, default=None: live_objects.get(name, default),
    )
    env = SimpleNamespace(scene=scene, robots=[[robot]], env_config={"action_frequency": 30})
    recorded_states = [previous["state"]]

    def deserialize(flat):
        assert live_objects["toy"]._recorded_non_kin_state_order == ("AABB",)
        return {0: deepcopy(recorded_states[int(flat[0])])}, len(flat)

    sim = SimpleNamespace(
        physics_sim_view=SimpleNamespace(update_articulations_kinematic=Mock()),
        sync_physx_to_fabric=Mock(),
        _refresh_state_caches=Mock(),
        dump_state=lambda **kwargs: th.zeros(1),
        deserialize=deserialize,
    )
    og = SimpleNamespace(sim=sim, clear=Mock())
    constructor = Mock(return_value=env)

    class Collector:
        def __init__(self, **kwargs):
            self.env, self.fps, self.max_state_size = env, 30, 0
            self.hdf5_file = h5py.File(kwargs["output_path"], "x")
            self.hdf5_file.create_group("data")
            self.current_traj_history = []

        def reset(self):
            pass

        def step(self, action):
            state = deepcopy(scene.save.return_value["state"])
            # A sparse frame can contain only the moving tool.
            state["registry"]["object_registry"] = {"toy": scene_objects(state)["toy"]}
            self.current_traj_history.append({
                "action": action.numpy(), "state": th.tensor([len(recorded_states)]), "state_size": 1,
            })
            recorded_states.append(state)

        def save_data(self):
            self.hdf5_file["data"].create_dataset(
                "action", data=[step["action"] for step in self.current_traj_history[1:]]
            )
            self.hdf5_file.close()

    def evaluate_predicate(predicate, *entities):
        return (
            predicate.__name__ == "Inside"
            and tuple(getattr(entity, "name", None) for entity in entities) == ("toy", "cabinet")
            and scene_objects(scene.save.return_value)["toy"]["root_link"]["pos"][0] < 2
        )

    modules = {
        "omnigibson": og,
        "omnigibson.envs": SimpleNamespace(Environment=constructor, HDF5CollectionWrapper=Collector),
        "omnigibson.macros": SimpleNamespace(gm=SimpleNamespace(unlocked=nullcontext)),
        "omnigibson.utils.config_utils": SimpleNamespace(TorchEncoder=json.JSONEncoder),
        "omnigibson.utils.python_utils": SimpleNamespace(
            recursively_convert_to_torch=lambda value: value,
            recursively_convert_from_torch=lambda value: value,
        ),
        "omnigibson.utils.motion_planning_utils": SimpleNamespace(detect_robot_collision_in_sim=lambda robot: False),
        "omnigibson.utils.bddl_utils": SimpleNamespace(evaluate_bddl_predicate=evaluate_predicate),
        "omnigibson.object_states.tensorized_state": SimpleNamespace(
            TensorizedState=SimpleNamespace(maybe_refresh_caches=Mock())
        ),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(wrappers, "SparseHDF5CollectionWrapper", Collector)
    monkeypatch.setattr("memory_bench.episodes.generate_bridges._build_config", lambda *args: {})
    monkeypatch.setattr("memory_bench.episodes.generate_bridges.robot_metadata", lambda robot: previous["robot"])
    monkeypatch.setattr(
        "memory_bench.utils.recording.reader.read_recording_metadata",
        lambda *args, **kwargs: {"interface": segments["key_1"]["interface"]},
    )

    def run_action(stage, step):
        if stage == "navigation" and outcome == "transient_motion":
            changed = deepcopy(previous)
            scene_objects(changed)["toy"]["root_link"]["pos"][0] = 1
            scene.save.return_value = changed
        if stage == "restore" and outcome in ("world_change", "broken_goal", "next_precondition", "allowed_motion"):
            changed = deepcopy(previous)
            scene_objects(changed)["toy"]["root_link"]["pos"][0] = 3 if outcome == "broken_goal" else 1
            scene.save.return_value = changed
        elif stage == "restore" and outcome in ("transient_motion", "transient_joint_velocity"):
            scene.save.return_value = previous
        if (stage, outcome) in (("navigation", "transient_joint_velocity"), ("restore", "terminal_joint_velocity")):
            changed = deepcopy(previous)
            scene_objects(changed)["toy"]["joint_vel"] = [-0.02739]
            scene.save.return_value = changed
        step(th.ones(23))
        if outcome == stage:
            raise RuntimeError("original bridge failure")

    joint_stages = iter(("stow", "restore"))

    def move_joints(robot, step, target, **velocity_limits):
        assert velocity_limits["control_grippers"] is True
        assert "world_guard" not in velocity_limits
        stage = next(joint_stages)
        run_action(stage, step)
        if (stage, outcome) in (("restore", "restore_contact"), ("stow", "blocked_retreat")):
            from memory_bench.utils.navigation.navigation import ArmContactError

            raise ArmContactError("test arm contact")

    def drive_to_pose(env, robot, pose, step, **velocity_limits):
        assert "world_guard" not in velocity_limits
        run_action("navigation", step)
        return {"frames": [1, 2]}

    monkeypatch.setattr("memory_bench.utils.navigation.navigation.move_joints", move_joints)
    monkeypatch.setattr("memory_bench.utils.navigation.navigation.drive_to_pose", drive_to_pose)
    if outcome == "blocked_retreat":
        monkeypatch.setattr(
            "memory_bench.utils.navigation.navigation.retreat_before_stow",
            Mock(side_effect=RuntimeError("test retreat blocked")),
        )
    output = session.paths.output_root / "bridge_00.hdf5"
    checks = {
        "completed_intervents": [("outfit_a_basic_toolbox", 0, {"pliers.n.01_1": "toy", "toolbox.n.01_1": "cabinet"})],
        "protected": {"toy"} if outcome in ("world_change", "transient_joint_velocity", "terminal_joint_velocity") else set(),
        "next_segment": {"identity": {"id": "key_1"}, "objects": {"read": ["toy"]}} if outcome == "next_precondition" else None,
    }
    if expected_error is None:
        manifest = generate_bridge(session, previous, previous, previous, output, **checks)
        assert manifest["source"]["frames"] == [0, expected_actions]
        assert manifest["motion"] == {"frames": [1, 2]}
        assert manifest["validation"]["stable"] is True
        assert manifest["objects"]["write"] == (["toy"] if outcome in ("allowed_motion", "transient_motion") else [])
        assert read_json(output.with_name("bridge_00_entry.json")) == previous
        assert read_json(output.with_name("bridge_00_exit.json")) == scene.save.return_value
        with h5py.File(output) as stream:
            np.testing.assert_array_equal(stream["data/action"][:], np.ones((expected_actions, 23)))
        constructor.assert_called_once()
        assert scene.save.call_count == 2
        scene.dump_state.assert_not_called()
        og.clear.assert_called_once()
        assert not session.paths.reports_dir.exists()
        return
    error_cls = ValueError if outcome == "next_precondition" else RuntimeError
    with pytest.raises(error_cls, match=expected_error) as caught:
        generate_bridge(session, previous, previous, previous, output, **checks)
    attempts = 2 if outcome == "blocked_retreat" else 1
    assert constructor.call_count == attempts
    assert type(caught.value).__name__ in capsys.readouterr().err
    assert not session.paths.reports_dir.exists()
    assert not output.exists()
    assert not output.with_name("bridge_00_entry.json").exists()
    assert not output.with_name("bridge_00_exit.json").exists()
    assert og.clear.call_count == attempts

