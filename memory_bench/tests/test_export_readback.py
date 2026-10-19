"""Readback rejects lost velocities, incomplete camera exports and broken segment evidence."""

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import h5py
import numpy as np
import pytest
import torch as th

from memory_bench.episodes.render_episodes import (
    _render_observation,
    export_contract,
    readback,
    validate_model_cameras,
)
from memory_bench.session import Session
from memory_bench.utils.paths import SessionPaths
from memory_bench.utils.scene_state import file_hash, read_json, write_json


@pytest.mark.parametrize("scene_offset", [0, 10])
def test_render_restores_attached_pose_and_camera_cache_without_physics(monkeypatch, scene_offset):
    objects = {
        "poster": SimpleNamespace(pose=None, attached_uuid=-1, update_visuals=Mock()),
        "robot": SimpleNamespace(update_visuals=Mock(), get_proprioception=Mock(return_value=(th.tensor([0.7]),))),
    }
    state = {
        "pos": th.zeros(3),
        "ori": th.tensor([0.0, 0.0, 0.0, 1.0]),
        "registry": {
            "object_registry": {
                "poster": {"root_link": {"pos": th.tensor([8.39, 2.87, 1.51])}, "attached_uuid": 123},
                "robot": {},
            }
        },
    }

    def load_state(recorded, serialized):
        assert serialized is False
        objects["poster"].attached_uuid = recorded["registry"]["object_registry"]["poster"]["attached_uuid"]
        # First-time AttachedTo loading aligns its child, even without any physics step.
        objects["poster"].pose = th.tensor([8.36 + scene_offset, 2.87, 1.51])

    def rebase(registry, relative_pos, relative_ori):
        assert relative_pos[0] == scene_offset
        result = deepcopy(registry)
        result["object_registry"]["poster"]["root_link"]["pos"][0] += scene_offset
        return result

    def load_kinematics(obj, recorded):
        if "root_link" in recorded:
            obj.pose = recorded["root_link"]["pos"].clone()

    scene = SimpleNamespace(
        load_state=Mock(side_effect=load_state),
        object_registry=lambda kind, name: objects[name],
        get_position_orientation=lambda: (th.tensor([float(scene_offset), 0, 0]), state["ori"]),
        _rebase_registry_state_poses=Mock(side_effect=rebase),
        objects=list(objects.values()),
        robots=[objects["robot"]],
    )
    expected = th.tensor([8.39 + scene_offset, 2.87, 1.51])
    fabric_link_pose = th.zeros(3)
    cached_camera_pose = th.zeros(3)

    def force_update(dt, time):
        fabric_link_pose.copy_(objects["poster"].pose)

    def update_world_xforms():
        cached_camera_pose.copy_(fabric_link_pose)

    def render():
        assert th.allclose(objects["poster"].pose, expected)
        # Correct pose getters do not imply that Hydra's camera hierarchy cache is current.
        assert th.allclose(cached_camera_pose, expected)
        assert objects["poster"].attached_uuid == 123

    sim = SimpleNamespace(
        physics_sim_view=SimpleNamespace(update_articulations_kinematic=Mock()),
        _sim_context=SimpleNamespace(
            _physx_fabric_interface=SimpleNamespace(force_update=Mock(side_effect=force_update))
        ),
        get_physics_dt=lambda: 1 / 120,
        current_time=1.0,
        _refresh_state_caches=Mock(),
        fabric_hierarchy=SimpleNamespace(update_world_xforms=Mock(side_effect=update_world_xforms)),
        render=Mock(side_effect=render),
        step=Mock(side_effect=AssertionError("Rendering must not step physics")),
    )
    monkeypatch.setitem(sys.modules, "omnigibson", SimpleNamespace(sim=sim))
    monkeypatch.setitem(
        sys.modules,
        "omnigibson.prims.entity_prim",
        SimpleNamespace(EntityPrim=SimpleNamespace(_load_state=load_kinematics)),
    )
    transforms = SimpleNamespace(
        invert_pose_transform=lambda pos, ori: (-pos, ori),
        pose_transform=lambda pos, ori, inv_pos, inv_ori: (pos + inv_pos, ori),
    )
    monkeypatch.setitem(sys.modules, "omnigibson.utils", SimpleNamespace(transform_utils=transforms))
    monkeypatch.setitem(
        sys.modules, "omnigibson.utils.python_utils", SimpleNamespace(recursively_convert_to_torch=lambda value: value)
    )
    env = SimpleNamespace(scene=scene, get_obs=Mock(return_value=([{"rgb": "rendered"}], [{"camera": "info"}])))
    observation, info, proprio = _render_observation(env, state, 3)
    assert observation == [{"rgb": "rendered"}]
    assert info == [{"obs_info": {"camera": "info"}}]
    assert proprio.tolist() == pytest.approx([0.7])
    assert sim.render.call_count == 3
    sim.step.assert_not_called()
    sim.fabric_hierarchy.update_world_xforms.assert_called_once()
    sim._sim_context._physx_fabric_interface.force_update.assert_called_once_with(1 / 120, 1.0)
    sim._refresh_state_caches.assert_called_once_with(dt=0.0)
    objects["poster"].update_visuals.assert_called_once()
    assert scene._rebase_registry_state_poses.call_count == bool(scene_offset)
    assert state["registry"]["object_registry"]["poster"]["root_link"]["pos"][0] == pytest.approx(8.39)


@pytest.fixture
def exported(tmp_path, monkeypatch):
    session = Session(
        {
            "session_id": "export_test",
            "validation": {"state_tolerances": {"ignored_fields": ["is_asleep"], "numeric": {"default": 1e-5}}},
        },
        SessionPaths(tmp_path, tmp_path, tmp_path),
    )
    root = tmp_path / "lerobot" / session.session_id
    write_json(
        root / "meta" / "omnigibson.json",
        {
            "cam_intrinsics": {name: np.eye(3).tolist() for name in ("head", "left", "right")},
            "omnigibson_git_hash": "fixture_revision",
        },
    )
    robot = SimpleNamespace(
        name="robot",
        base_control_idx=np.array([0, 1, 5]),
        base_idx=np.arange(6),
        arm_control_idx={"left": np.array([6, 7])},
        is_holonomic_base=True,
        proprioception_dim=10,
    )
    proprio_indices = {
        "base_qvel": slice(0, 3),
        "arm_left_qpos": slice(3, 5),
        "arm_left_qvel": slice(5, 7),
        "eef_left_pos": slice(7, 10),
    }
    sensors, mapping = [], {}
    for camera in ("head", "left", "right"):
        namespace = f"robot::{camera}"
        sensors.append(
            (
                namespace,
                SimpleNamespace(
                    all_modalities={"rgb", "depth_linear"},
                    modalities={"rgb", "depth_linear"},
                    image_height=2,
                    image_width=3,
                ),
            )
        )
        for modality in ("rgb", "depth_linear"):
            mapping[f"{namespace}::{modality}"] = f"observation.{modality}.{camera}"
    contract = export_contract(robot, sensors, mapping, proprio_indices, 30, ["proprio", "rgb", "depth_linear"])
    annotations, states, samples = [], [], []
    for frame, segment_id in enumerate(("intervent_1", "bridge_00", "intervent_2", "intervent_2")):
        robot_state = {
            "root_link": {"pos": [frame, 0, 0], "ori": [0, 0, 0, 1], "lin_vel": [1, 2, 0], "ang_vel": [0, 0, 3]},
            "joint_pos": [0, 0, 0, 0, 0, np.pi / 2, frame + 0.1, frame + 0.2],
            "joint_vel": [1, 2, 0, 0, 0, 3, 0.3, -0.4],
        }
        state = {
            "registry": {
                "object_registry": {
                    "robot": robot_state,
                    "toy": {
                        "root_link": {"pos": [int(frame > 0), 0, 0], "ori": [0, 0, 0, 1]},
                        "non_kin": {"ToggledOn": {"value": frame > 0}},
                    },
                    "hammer": {
                        "root_link": {"pos": [int(frame == 3), 0, 0], "ori": [0, 0, 0, 1]},
                        "non_kin": {"AttachedTo": {"attached_obj_uuid": 123 if frame == 3 else -1}},
                    },
                },
                "system_registry": {},
            }
        }
        states.append(state)
        role = "bridge_task" if segment_id == "bridge_00" else "intervent_task"
        instruction = "second" if frame else "first"
        if frame < 3:
            annotations.append(
                {
                    "id": segment_id,
                    "role": role,
                    "frames": [frame, frame + 1 if frame < 2 else 4],
                    "objects": {"write": ["toy"] if not frame else (["hammer"] if frame == 2 else [])},
                    "instruction": instruction,
                    "bindings": {},
                }
            )
        sample = {
            "episode_index": th.tensor(0),
            "timestamp": th.tensor(frame / 30),
            "action": th.tensor([frame], dtype=th.float32),
            "task": instruction,
            # yaw=pi/2 rotates world [1,2] into body [2,-1]; FK-only EEF slots are left intact.
            "observation.state": th.tensor([2, -1, 3, frame + 0.1, frame + 0.2, 0.3, -0.4, 9, 8, 7]),
        }
        for key, video in contract["videos"].items():
            height, width, channels = video["shape"]
            sample[key] = th.zeros(channels, height, width)
        samples.append(sample)
    source = tmp_path / "episode.hdf5"
    with h5py.File(source, "x") as stream:
        group = stream.create_group("data/demo_0")
        group.create_dataset("action", data=np.arange(4, dtype=np.float32).reshape(-1, 1))
        group.create_dataset("instruction", data=[sample["task"] for sample in samples], dtype=h5py.string_dtype())
        group.create_dataset(
            "state_json", data=[json.dumps(state) for state in [*states, states[-1]]], dtype=h5py.string_dtype()
        )
    recipe = {
        "episode_id": "episode_00000000",
        "hdf5": source.name,
        "fps": 30,
        "total_frames": 4,
        "memory_spans": [],
        "annotations": annotations,
    }
    annotation = deepcopy(recipe)
    for frame, segment in enumerate(annotation["annotations"]):
        segment["start_snapshot"] = f"segment_states/seg_{frame:02d}.json"
        scene = {"state": deepcopy(states[frame])}
        recorded_robot = scene["state"]["registry"]["object_registry"]["robot"]
        recorded_robot["joint_vel"] = [0] * 8
        recorded_robot["root_link"]["lin_vel"] = [0] * 3
        recorded_robot["root_link"]["ang_vel"] = [0] * 3
        write_json(root / segment["start_snapshot"], {"scene": scene, "bindings": {}})
    annotation["last_observation_snapshot"] = "segment_states/last_observation.json"
    write_json(
        root / annotation["last_observation_snapshot"],
        {
            "scene": {"state": states[-1]},
            "bindings": {},
            "frame": 3,
            "segment_id": "intervent_2",
            "state_convention": "pre_action",
        },
    )
    annotation_path = root / "annotations" / "episode_00000000.json"
    write_json(annotation_path, annotation)
    contract["source_sha256"] = file_hash(source)
    write_json(root / "export_contracts" / "episode_00000000.json", contract)
    metadata = SimpleNamespace(
        total_episodes=1,
        fps=30,
        video_keys=list(contract["videos"]),
        features={
            key: {"shape": video["shape"], "info": {"video.fps": 30}} for key, video in contract["videos"].items()
        },
        get_video_file_path=Mock(side_effect=lambda episode, key: Path("videos") / key / f"episode_{episode}.mp4"),
        video_probe=Mock(return_value={"video.fps": 30}),
    )

    class Dataset:
        meta = metadata

        def __len__(self):
            return len(samples)

        def __getitem__(self, index):
            return samples[index]

    monkeypatch.setitem(
        sys.modules, "lerobot.datasets", SimpleNamespace(LeRobotDataset=lambda *args, **kwargs: Dataset())
    )
    monkeypatch.setitem(
        sys.modules, "lerobot.datasets.video_utils", SimpleNamespace(get_video_info=metadata.video_probe)
    )
    return session, recipe, root, samples, metadata, contract


def test_readback_accepts_nonzero_body_frame_velocities_and_inherited_snapshots(exported):
    session, recipe, _, _, _, contract = exported
    assert contract["proprio"][0]["yaw_joint"] == 5
    assert len(contract["videos"]) == 6
    report = readback(session, [recipe])
    assert report["readback"] == "passed"
    assert report["frames"] == 4
    assert report["last_observation_snapshots"][0]["frame"] == recipe["total_frames"] - 1


def test_readback_probes_actual_depth_video_when_metadata_has_no_fps(exported):
    session, recipe, root, _, metadata, _ = exported
    key = "observation.depth_linear.head"
    metadata.features[key]["info"] = {"is_depth_map": True, "video.output_unit": "m"}
    report = readback(session, [recipe])
    metadata.get_video_file_path.assert_called_once_with(0, key)
    path = root / "videos" / key / "episode_0.mp4"
    metadata.video_probe.assert_called_once_with(path)
    assert report["video_fps_probes"] == [{"episode_index": 0, "key": key, "path": str(path), "fps": 30}]


@pytest.mark.parametrize("defect", ["missing_camera", "invalid_matrix", "missing_revision"])
def test_readback_requires_persisted_simulator_and_camera_metadata(exported, defect):
    session, recipe, root, _, _, _ = exported
    path = root / "meta" / "omnigibson.json"
    metadata = read_json(path)
    if defect == "missing_camera":
        del metadata["cam_intrinsics"]["head"]
    elif defect == "invalid_matrix":
        metadata["cam_intrinsics"]["head"][0][0] = -1
    else:
        metadata["omnigibson_git_hash"] = ""
    path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="intrinsics|git hash"):
        readback(session, [recipe])


def test_external_camera_contract_uses_external_namespace_and_its_own_modalities():
    sensor = SimpleNamespace(all_modalities={"rgb", "depth_linear"}, modalities={"rgb"}, image_height=2, image_width=3)
    contract = export_contract(
        SimpleNamespace(name="robot", proprioception_dim=0),
        [("external::camera", sensor)],
        {"external::camera::rgb": "observation.rgb.camera"},
        {},
        30,
        ["rgb", "depth_linear"],
    )
    assert contract["videos"] == {"observation.rgb.camera": {"shape": [2, 3, 3], "fps": 30}}


def test_authored_camera_inventory_ignores_runtime_auxiliary_prim_but_requires_model_sensors():
    class Camera:
        all_modalities = {"rgb", "depth_linear"}

        def __init__(self, size):
            self.image_height = self.image_width = size

    camera_prim = SimpleNamespace(
        GetName=lambda: "Camera", GetPrimTypeInfo=lambda: SimpleNamespace(GetTypeName=lambda: "Camera")
    )
    asset_links = {
        "base": SimpleNamespace(GetChildren=lambda: []),
        "head": SimpleNamespace(GetChildren=lambda: [camera_prim]),
        "hand": SimpleNamespace(GetChildren=lambda: [camera_prim]),
    }
    stage = SimpleNamespace(GetDefaultPrim=lambda: SimpleNamespace(GetChild=asset_links.get))
    rendering = {
        "obs_modalities": ["proprio", "rgb"],
        "sensor_config": {
            "VisionSensor": {"sensor_kwargs": {"image_height": 480, "image_width": 480}},
            "head:Camera:0": {"sensor_kwargs": {"image_height": 720, "image_width": 720}},
        },
    }
    robot = SimpleNamespace(
        name="robot",
        _include_sensor_names=None,
        _exclude_sensor_names=None,
        _generate_sensor_config=lambda custom_config: custom_config,
        links={
            name: SimpleNamespace(
                prim_path=f"/robot/{name}",
                # The live base has an auxiliary Camera absent from the authored asset.
                prim=SimpleNamespace(GetChildren=lambda: [camera_prim]),
            )
            for name in asset_links
        },
        sensors={"robot:head:Camera:0": Camera(720), "robot:hand:Camera:0": Camera(480)},
    )
    expected = validate_model_cameras(robot, stage, rendering, Camera)
    assert set(expected) == set(robot.sensors)
    hand = robot.sensors.pop("robot:hand:Camera:0")
    with pytest.raises(ValueError, match="model camera was not created"):
        validate_model_cameras(robot, stage, rendering, Camera)
    robot._exclude_sensor_names = {"/hand/"}
    assert set(validate_model_cameras(robot, stage, rendering, Camera)) == {"robot:head:Camera:0"}
    robot._exclude_sensor_names = None
    robot.sensors["robot:hand:Camera:0"] = hand
    robot.sensors["robot:head:Camera:0"].image_height = 480
    with pytest.raises(ValueError, match="configuration was not applied"):
        validate_model_cameras(robot, stage, rendering, Camera)


@pytest.mark.parametrize("defect", ["nonkin", "inherited_pose", "missing_file", "post_action_index"])
def test_readback_rejects_invalid_last_observation_snapshot(exported, defect):
    session, recipe, root, _, _, _ = exported
    annotation = read_json(root / "annotations" / "episode_00000000.json")
    path = root / annotation["last_observation_snapshot"]
    snapshot = read_json(path)
    objects = snapshot["scene"]["state"]["registry"]["object_registry"]
    if defect == "missing_file":
        path.unlink()
    else:
        if defect == "nonkin":
            objects["hammer"]["non_kin"]["AttachedTo"]["attached_obj_uuid"] = -1
        elif defect == "inherited_pose":
            objects["toy"]["root_link"]["pos"][0] = 0
        else:
            snapshot["frame"] = recipe["total_frames"]
        path.write_text(json.dumps(snapshot))
    if defect == "missing_file":
        with pytest.raises(FileNotFoundError):
            readback(session, [recipe])
    else:
        with pytest.raises(ValueError, match="Last observation snapshot"):
            readback(session, [recipe])


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("zero_qvel", "Proprioception mismatch"),
        ("missing_camera", "video keys"),
        ("wrong_video_shape", "Video frame shape"),
        ("wrong_fps", "Video FPS"),
        ("missing_fps_wrong_probe", "Video FPS"),
        ("bad_snapshot", "inherited state mismatch"),
        ("bad_annotations", "contiguously"),
    ],
)
def test_readback_rejects_export_corruption(exported, defect, message):
    session, recipe, root, samples, metadata, _ = exported
    if defect == "zero_qvel":
        samples[0]["observation.state"][:3] = 0
    elif defect == "missing_camera":
        metadata.video_keys.pop()
    elif defect == "wrong_video_shape":
        samples[1][metadata.video_keys[0]] = th.zeros(2, 3, 3)
    elif defect == "wrong_fps":
        metadata.features[metadata.video_keys[0]]["info"]["video.fps"] = 29
    elif defect == "missing_fps_wrong_probe":
        metadata.features[metadata.video_keys[0]]["info"].pop("video.fps")
        metadata.video_probe.return_value = {"video.fps": 29}
    elif defect == "bad_snapshot":
        path = root / "segment_states" / "seg_01.json"
        snapshot = read_json(path)
        snapshot["scene"]["state"]["registry"]["object_registry"]["toy"]["root_link"]["pos"][0] = 9
        path.write_text(json.dumps(snapshot))
    else:
        path = root / "annotations" / "episode_00000000.json"
        annotation = read_json(path)
        annotation["annotations"][1]["frames"][0] = 0
        path.write_text(json.dumps(annotation))
    with pytest.raises(ValueError, match=message):
        readback(session, [recipe])
