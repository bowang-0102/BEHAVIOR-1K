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

from memory_bench.episodes.replay_episodes import (
    _render_observation,
    validate_model_cameras,
)
from memory_bench.session import Session
from memory_bench.utils.episode_recipe import episode_annotations, validate_annotations
from memory_bench.tools import validate_export
from memory_bench.tools.validate_export import readback
from memory_bench.utils.paths import SessionPaths
from memory_bench.utils.scene_state import read_json, write_json


@pytest.mark.parametrize("scene_offset", [0, 10])
@pytest.mark.parametrize("selection", ["full", "filtered", "failure"])
def test_render_restores_attached_pose_and_camera_cache_without_physics(monkeypatch, scene_offset, selection):
    objects = {
        "poster": SimpleNamespace(pose=None, attached_uuid=-1, update_visuals=Mock()),
        "robot": SimpleNamespace(update_visuals=Mock(), get_proprioception=Mock(return_value=(th.tensor([0.7]),))),
        "table": SimpleNamespace(pose=th.tensor([1.0, 2.0, 3.0]), update_visuals=Mock()),
    }
    state = {
        "pos": th.zeros(3),
        "ori": th.tensor([0.0, 0.0, 0.0, 1.0]),
        "registry": {
            "object_registry": {
                "poster": {"root_link": {"pos": th.tensor([8.39, 2.87, 1.51])}, "attached_uuid": 123},
                "robot": {},
                "table": {"root_link": {"pos": th.tensor([9.0, 2.0, 3.0])}},
            }
        },
    }
    for name, obj in objects.items():
        obj.name = name

    class ObjectRegistry:
        _load_filter = staticmethod(lambda obj: obj.name != "table")

        def __call__(self, kind, name):
            return objects[name]

        def set_load_filter(self, value):
            self._load_filter = value

    object_registry = ObjectRegistry()
    original_filter = object_registry._load_filter

    def load_state(recorded, serialized):
        assert serialized is False
        assert set(recorded["registry"]["object_registry"]) == set(objects)
        assert object_registry._load_filter(objects["poster"])
        assert not object_registry._load_filter(objects["table"])
        assert object_registry._load_filter(objects["robot"]) == (selection == "full")
        if selection == "failure":
            raise RuntimeError("state load failed")
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
        object_registry=object_registry,
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
    names = {} if selection == "full" else {"load_names": {"poster", "table"}, "repair_names": {"poster", "robot"}}
    if selection == "failure":
        with pytest.raises(RuntimeError, match="state load failed"):
            _render_observation(env, state, 3, **names)
        assert object_registry._load_filter is original_filter
        sim.render.assert_not_called()
        return
    observation, info, proprio = _render_observation(env, state, 3, **names)
    assert object_registry._load_filter is original_filter
    assert objects["table"].pose[0] == (9 if selection == "full" else 1)
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
        },
        SessionPaths(tmp_path, tmp_path, tmp_path, "export_test", "house"),
    )
    root = session.paths.export_dir
    write_json(
        root / "meta" / "omnigibson.json",
        {
            "cam_intrinsics": {name: np.eye(3).tolist() for name in ("head", "left", "right")},
            "omnigibson_git_hash": "fixture_revision",
        },
    )
    videos = {
        f"observation.{modality}.{camera}": {
            "dtype": "video", "shape": [2, 3, channels], "info": {"video.fps": 30},
        }
        for camera in ("head", "left", "right")
        for modality, channels in (("rgb", 3), ("depth_linear", 1))
    }
    annotations, samples = [], []
    for frame, segment_id in enumerate(("key_1", "bridge_00", "query", "query")):
        role = {"key_1": "key_task", "bridge_00": "bridge_task", "query": "query_task"}[segment_id]
        instruction = "" if role == "bridge_task" else ("second" if frame else "first")
        if frame < 3:
            annotations.append({
                "id": segment_id, "role": role, "activity": "test_activity", "frames": [frame, frame + 1 if frame < 2 else 4],
                "source": {"hdf5": "episode.hdf5", "demo": 0, "frames": [frame, frame + 1 if frame < 2 else 4]},
                "objects": {"used": [], "read": [], "write": []}, "instruction": instruction,
            })
        sample = {
            "episode_index": th.tensor(0),
            "timestamp": th.tensor(frame / 30),
            "action": th.tensor([frame], dtype=th.float32),
            "task": instruction,
            "observation.state": th.tensor([2, -1, 3, frame + 0.1, frame + 0.2, 0.3, -0.4, 9, 8, 7]),
        }
        for key, video in videos.items():
            height, width, channels = video["shape"]
            sample[key] = th.zeros(channels, height, width)
        samples.append(sample)
    source = tmp_path / "episode.hdf5"
    with h5py.File(source, "x") as stream:
        group = stream.create_group("data/demo_0")
        group.create_dataset("action", data=np.arange(4, dtype=np.float32).reshape(-1, 1))
        group.create_dataset("instruction", data=[sample["task"] for sample in samples], dtype=h5py.string_dtype())
    recipe = {
        "session_id": session.session_id, "episode_id": "episode_00000000", "instance_id": 1,
        "instance": "instance.json", "segments": annotations,
        "output": {"hdf5": source.name, "replay_scene": "scene.json", "query_start": "query.json",
                   "query_start_frame": 2, "fps": 30, "total_frames": 4},
    }
    write_json(root / "annotations" / "episode_00000000.json", episode_annotations(recipe, 0))
    metadata = SimpleNamespace(
        total_episodes=1,
        fps=30,
        video_keys=list(videos),
        features={**videos, "observation.state": {"dtype": "float32", "shape": [10]}},
        get_video_file_path=Mock(side_effect=lambda episode, key: Path("videos") / key / f"episode_{episode}.mp4"),
        video_probe=Mock(return_value={"video.fps": 30}),
    )

    class Dataset:
        meta = metadata

        def __init__(self, repo_id, root, episodes=None):
            self.episodes = episodes
            self.samples = [sample for sample in samples if episodes is None or int(sample["episode_index"]) in episodes]

        @property
        def num_episodes(self):
            return metadata.total_episodes if self.episodes is None else len(self.episodes)

        def __len__(self):
            return len(self.samples)

        def __getitem__(self, index):
            metadata.read_episodes.append(int(self.samples[index]["episode_index"]))
            return self.samples[index]

    metadata.read_episodes = []
    monkeypatch.setitem(
        sys.modules, "lerobot.datasets", SimpleNamespace(LeRobotDataset=Dataset)
    )
    monkeypatch.setitem(
        sys.modules, "lerobot.datasets.video_utils", SimpleNamespace(get_video_info=metadata.video_probe)
    )
    return session, recipe, root, samples, metadata, videos


def test_readback_uses_dataset_metadata_without_export_sidecars(exported):
    session, recipe, root, _, _, videos = exported
    report = readback(session, [recipe])
    assert report["readback"] == "passed"
    assert report["frames"] == 4
    assert len(videos) == 6
    assert not (root / "segment_states").exists()
    assert not (root / "export_contracts").exists()
    assert "last_observation_snapshots" not in report


def test_training_annotations_are_self_contained(exported):
    session, recipe, root, _, _, _ = exported
    annotation = read_json(root / "annotations" / "episode_00000000.json")
    assert list(annotation) == [
        "session_id", "episode_id", "instance_id", "episode_index", "fps", "total_frames", "segments", "memory_spans",
    ]
    assert annotation["memory_spans"] == [{
        "key_id": "key_1", "query_id": "query", "key_end_frame": 1, "query_start_frame": 2,
    }]
    for segment in annotation["segments"]:
        assert list(segment) == ["id", "role", "activity", "instruction", "frames"]
        if segment["role"] == "bridge_task":
            assert segment["instruction"] == ""
    # Labels remain valid without any source recording or instance files.
    session.paths.resolve(recipe["output"]["hdf5"]).unlink()
    validate_annotations(annotation)


@pytest.mark.parametrize("episode_ids", [None, [2], [2, 0]])
def test_validation_command_selects_original_episode_ids_and_can_repeat(exported, monkeypatch, capsys, episode_ids):
    session, recipe, root, samples, metadata, _ = exported
    selected = list(range(3)) if episode_ids is None else sorted(episode_ids)
    original_samples = list(samples)
    samples[:] = [
        {**sample, "episode_index": th.tensor(index)} for index in range(3) for sample in original_samples
    ]
    metadata.total_episodes = 3
    annotation = read_json(root / "annotations" / f"{recipe['episode_id']}.json")
    for index in selected:
        episode_id = f"episode_{index:08d}"
        write_json(session.paths.recipes_dir / f"{episode_id}.json", {**recipe, "episode_id": episode_id})
        (root / "annotations" / f"{episode_id}.json").write_text(json.dumps({
            **annotation, "episode_id": episode_id, "episode_index": index,
        }))
    key = metadata.video_keys[0]
    metadata.features[key]["info"].pop("video.fps")
    monkeypatch.setattr(Session, "instance_count", property(lambda self: 3))
    monkeypatch.setattr(Session, "load", Mock(return_value=session))
    argv = ["validate_export", "session.yaml"]
    if episode_ids is not None:
        argv.extend(["--episode-id", *map(str, episode_ids)])
    monkeypatch.setattr(sys, "argv", argv)

    validate_export.main()

    report = json.loads(capsys.readouterr().out)
    assert report["episode_ids"] == [f"episode_{index:08d}" for index in selected]
    assert report["episodes"] == len(selected)
    assert report["frames"] == 4 * len(selected)
    assert metadata.read_episodes == [index for index in selected for _ in range(4)]
    assert [call.args[0] for call in metadata.get_video_file_path.call_args_list] == selected
    assert read_json(session.paths.reports_dir / "export_readback.json") == report

    validate_export.main()
    assert json.loads(capsys.readouterr().out) == report


@pytest.mark.parametrize("episode_id", [-1, 3])
def test_validation_command_rejects_out_of_range_episode(exported, monkeypatch, episode_id):
    session, _, _, _, _, _ = exported
    monkeypatch.setattr(Session, "instance_count", property(lambda self: 3))
    monkeypatch.setattr(Session, "load", Mock(return_value=session))
    monkeypatch.setattr(sys, "argv", ["validate_export", "session.yaml", "--episode-id", str(episode_id)])
    with pytest.raises(SystemExit) as error:
        validate_export.main()
    assert error.value.code == 2


@pytest.mark.parametrize("select_episode", [False, True])
def test_validation_maps_single_instance_to_export_index_zero(exported, monkeypatch, capsys, select_episode):
    session, recipe, root, _, metadata, _ = exported
    episode_id = "episode_00000004"
    recipe.update(episode_id=episode_id, instance_id=5)
    write_json(session.paths.recipes_dir / f"{episode_id}.json", recipe)
    annotation_path = root / "annotations" / "episode_00000000.json"
    annotation = read_json(annotation_path)
    annotation.update(episode_id=episode_id, instance_id=5, episode_index=0)
    write_json(annotation_path.with_name(f"{episode_id}.json"), annotation)
    annotation_path.unlink()
    key = metadata.video_keys[0]
    metadata.features[key]["info"].pop("video.fps")
    monkeypatch.setattr(Session, "instance_count", property(lambda self: 5))
    monkeypatch.setattr(Session, "load", Mock(return_value=session))
    argv = ["validate_export", "session.yaml"]
    if select_episode:
        argv.extend(["--episode-id", "4"])
    monkeypatch.setattr(sys, "argv", argv)

    validate_export.main()

    report = json.loads(capsys.readouterr().out)
    assert report["episode_ids"] == [episode_id]
    assert report["frames"] == recipe["output"]["total_frames"]
    assert metadata.read_episodes == [0] * recipe["output"]["total_frames"]
    metadata.get_video_file_path.assert_called_once_with(0, key)


def test_readback_probes_actual_depth_video_when_metadata_has_no_fps(exported):
    session, recipe, root, _, metadata, _ = exported
    key = "observation.depth_linear.head"
    metadata.features[key]["info"] = {"is_depth_map": True, "video.output_unit": "m"}
    report = readback(session, [recipe])
    metadata.get_video_file_path.assert_called_once_with(0, key)
    path = root / "videos" / key / "episode_0.mp4"
    metadata.video_probe.assert_called_once_with(path)
    assert report["video_fps_probes"] == [{
        "episode_index": 0, "key": key, "path": str(path.relative_to(session.paths.output_root)), "fps": 30,
    }]


def test_readback_checks_frame_instructions_against_segment_labels(exported):
    session, recipe, root, _, _, _ = exported
    recipe["segments"][1]["instruction"] = "incorrect bridge instruction"
    (root / "annotations" / "episode_00000000.json").write_text(json.dumps(episode_annotations(recipe, 0)))
    with pytest.raises(ValueError, match="Instruction differs from segment"):
        readback(session, [recipe])


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


def test_authored_camera_inventory_ignores_runtime_auxiliary_prim_but_requires_model_sensors(monkeypatch):
    class Camera:
        all_modalities = {"rgb", "depth_linear"}

        def __init__(self, size):
            self.image_height = self.image_width = size

    monkeypatch.setitem(sys.modules, "omnigibson.sensors.vision_sensor", SimpleNamespace(VisionSensor=Camera))
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
    expected = validate_model_cameras(robot, stage, rendering)
    assert set(expected) == set(robot.sensors)
    hand = robot.sensors.pop("robot:hand:Camera:0")
    with pytest.raises(ValueError, match="model camera was not created"):
        validate_model_cameras(robot, stage, rendering)
    robot._exclude_sensor_names = {"/hand/"}
    assert set(validate_model_cameras(robot, stage, rendering)) == {"robot:head:Camera:0"}
    robot._exclude_sensor_names = None
    robot.sensors["robot:hand:Camera:0"] = hand
    robot.sensors["robot:head:Camera:0"].image_height = 480
    with pytest.raises(ValueError, match="configuration was not applied"):
        validate_model_cameras(robot, stage, rendering)


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("wrong_proprio_shape", "Proprioception shape"),
        ("nonfinite_proprio", "non-finite"),
        ("wrong_action", "Action mismatch"),
        ("wrong_instruction", "Instruction mismatch"),
        ("wrong_timestamp", "Timestamp mismatch"),
        ("missing_camera", "video keys"),
        ("wrong_video_shape", "Video frame shape"),
        ("wrong_fps", "Video FPS"),
        ("missing_fps_wrong_probe", "Video FPS"),
        ("bad_annotations", "contiguously"),
        ("bad_memory_span", "Invalid memory span"),
        ("external_recipe_path", "annotations differ"),
    ],
)
def test_readback_rejects_export_corruption(exported, defect, message):
    session, recipe, root, samples, metadata, _ = exported
    if defect == "wrong_proprio_shape":
        samples[0]["observation.state"] = th.zeros(9)
    elif defect == "nonfinite_proprio":
        samples[0]["observation.state"][0] = float("nan")
    elif defect == "wrong_action":
        samples[1]["action"][0] = -1
    elif defect == "wrong_instruction":
        samples[0]["task"] = "wrong"
    elif defect == "wrong_timestamp":
        samples[0]["timestamp"] = th.tensor(1.0)
    elif defect == "missing_camera":
        metadata.video_keys.pop()
    elif defect == "wrong_video_shape":
        samples[1][metadata.video_keys[0]] = th.zeros(2, 3, 3)
    elif defect == "wrong_fps":
        metadata.features[metadata.video_keys[0]]["info"]["video.fps"] = 29
    elif defect == "missing_fps_wrong_probe":
        metadata.features[metadata.video_keys[0]]["info"].pop("video.fps")
        metadata.video_probe.return_value = {"video.fps": 29}
    else:
        path = root / "annotations" / "episode_00000000.json"
        annotation = read_json(path)
        if defect == "bad_memory_span":
            annotation["memory_spans"][0]["key_end_frame"] = 2
        elif defect == "external_recipe_path":
            annotation["recipe"] = "/outside/recipe.json"
        else:
            annotation["segments"][1]["frames"][0] = 0
        path.write_text(json.dumps(annotation))
    with pytest.raises(ValueError, match=message):
        readback(session, [recipe])
