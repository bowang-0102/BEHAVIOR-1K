"""CPU-only task-22 layout screening; no simulator, rendering, or navigation execution.

Uses the furnished static floor map, recorded first-frame shoe collision bounds, and
a fixed rectangular robot footprint. Passing is a region-connectivity prefilter,
not a guarantee for a later episode's live collision map or exact task endpoints.
"""

import argparse
import json
from hashlib import md5
from itertools import product
from pathlib import Path

import cv2
import h5py
import numpy as np
from scipy.spatial.transform import Rotation

from memory_bench.session import Session
from memory_bench.utils.navigation.navigation_map import erode_footprint, rasterize_mesh_bounds


def recorded_root(row, name):
    """Read a rigid root from the recorded registry's UUID-prefixed state layout."""
    # Match OmniGibson get_uuid(name); this is an object identifier, not a file hash.
    uuid = np.float32(int(md5(name.encode()).hexdigest(), 16) % 10**8)
    matches = np.flatnonzero(row == uuid)
    if len(matches) != 1:
        raise ValueError(f"Expected one recorded state for {name}, found {len(matches)}")
    start = int(matches[0]) + 1
    state = row[start:start + 14]
    if (
        len(state) != 14 or not np.isfinite(state).all() or state[0] not in (0, 1)
        or not np.isclose(np.linalg.norm(state[4:8]), 1.0, atol=1e-3)
    ):
        raise ValueError(f"Invalid rigid-root state for {name}")
    return state[1:4], Rotation.from_quat(state[4:8]), start + 14


def read_layout(path, session, asset_root):
    """Extract the four shoes and the R1Pro base entry without deserializing in Isaac Sim."""
    with h5py.File(path, "r") as stream:
        data = stream["data"]
        config = json.loads(data.attrs["config"])
        scene = json.loads(data.attrs["scene_file"])
        if config["task"]["activity_name"] != "putting_shoes_on_rack" or (
            config["scene"]["scene_model"] != session.scene_model
        ):
            raise ValueError("Recording activity or scene differs from the session")
        demos = [
            int(name.removeprefix("demo_")) for name, group in data.items()
            if name.startswith("demo_") and "action" in group and len(group["action"])
        ]
        if not demos:
            raise ValueError("No demo contains actions")
        demo = max(demos)
        group = data[f"demo_{demo}"]
        if len(group["init_metadata"]) or any(json.loads(group.attrs["transitions"]).values()):
            raise ValueError("Recording has object overrides or runtime topology changes")
        row = group["state"][0, :int(group["state_size"][0])]

    if not np.allclose(row[:7], [0, 0, 0, 0, 0, 0, 1]):
        raise ValueError("Static floor screening requires a scene at the world origin")
    bindings = scene["metadata"]["task"]["inst_to_name"]
    shoes = sorted(name for role, name in bindings.items() if role.startswith(("gym_shoe.", "sandal.")))
    if len(shoes) != 4:
        raise ValueError("Expected the four task-22 shoes")
    corners = np.asarray(list(product((-0.5, 0.5), repeat=3)))
    bounds = []
    positions = {}
    for name in shoes:
        args = scene["objects_info"]["init_info"][name]["args"]
        state = scene["state"]["registry"]["object_registry"][name]
        metadata_path = asset_root / "objects" / args["category"] / args["model"] / "misc/metadata.json"
        metadata = json.loads(metadata_path.read_text())
        if state["non_kin"] or "joint_pos" in state or set(metadata["link_bounding_boxes"]) != {"base_link"}:
            raise ValueError(f"Static screening requires a rigid, single-link shoe: {name}")
        box = metadata["link_bounding_boxes"]["base_link"]["collision"]["axis_aligned"]
        transform = np.asarray(box["transform"])
        points = (corners * box["extent"]) @ transform[:3, :3].T + transform[:3, 3]
        position, rotation, _ = recorded_root(row, name)
        points = rotation.apply(points * args["scale"]) + position
        bounds.append((points.min(axis=0), points.max(axis=0)))
        positions[name] = position.tolist()

    robot = config["robots"][0]
    model = robot["model"] if "model" in robot else robot["type"]
    if model.lower() != "r1pro":
        raise ValueError("Static entry extraction supports the recorded R1Pro base joints")
    position, rotation, joint_start = recorded_root(row, robot["name"])
    # The first three virtual joints translate the base relative to its fixed root.
    entry = position + rotation.apply(row[joint_start:joint_start + 3])
    return {"hdf5": str(path.resolve()), "demo": demo}, bounds, positions, entry


def screen_session(session, output_dir, half_extent):
    if session.scene_model != "house_double_floor_lower" or session.config.get("ignored_doors", []):
        raise ValueError("Static screening supports house_double_floor_lower with the original doors")
    segment_id, = [
        key for key, segment in session.segments.items()
        if segment["role"] == "intervent_task" and segment["source"]["task_id"] == 22
    ]
    output_path = output_dir / f"{session.session_id}.json"
    if output_path.exists():
        raise FileExistsError(f"Screening report already exists: {output_path}")
    asset_root = session.paths.omnigibson_data_root / "behavior-1k-assets"
    layout = asset_root / "scenes" / session.scene_model / "layout"
    resolution = float(session.bridge.get("map_resolution", 0.02))
    image = cv2.imread(str(layout / "floor_trav_0.png"), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(layout / "floor_trav_0.png")
    size = int(image.shape[0] * 0.01 / resolution)
    floor = cv2.resize(image, (size, size))
    floor[floor < 255] = 0
    instances = cv2.imread(str(layout / "floor_insseg_0.png"), cv2.IMREAD_GRAYSCALE)
    semantics = cv2.imread(str(layout / "floor_semseg_0.png"), cv2.IMREAD_GRAYSCALE)
    if instances is None or semantics is None:
        raise FileNotFoundError(f"Missing room segmentation in {layout}")
    instances = cv2.resize(instances, (size, size), interpolation=cv2.INTER_NEAREST)
    semantics = cv2.resize(semantics, (size, size), interpolation=cv2.INTER_NEAREST)
    categories = (asset_root / "metadata/room_categories.txt").read_text().splitlines()
    room_ids, counts = {}, {}
    for instance_id in np.unique(instances)[1:]:
        row, column = np.argwhere(instances == instance_id)[0]
        category = categories[int(semantics[row, column]) - 1]
        index = counts.get(category, 0)
        room_ids[f"{category}_{index}"] = int(instance_id)
        counts[category] = index + 1
    rooms = sorted({segment["room_id"] for segment in session.segments.values()})
    selected_rooms = np.isin(instances, [room_ids[room] for room in rooms])
    # Exclude other room instances, retaining unlabeled doorway cells between selected rooms.
    floor[(instances != 0) & ~selected_rooms] = 0
    rows, columns = np.where(selected_rooms)
    margin = int(np.ceil(max(half_extent) / resolution)) + 2
    low = np.maximum([rows.min() - margin, columns.min() - margin], 0)
    high = np.minimum([rows.max() + margin + 1, columns.max() + margin + 1], size)
    region = (slice(low[0], high[0]), slice(low[1], high[1]))
    baseline, _ = erode_footprint(floor, -half_extent, half_extent, resolution)
    _, labels = cv2.connectedComponents((baseline[region] != 0).astype(np.uint8), connectivity=4)
    common = set(np.unique(labels)) - {0}
    for room in rooms:
        common &= set(np.unique(labels[instances[region] == room_ids[room]]))
    if not common:
        raise ValueError("Static baseline already disconnects the task rooms without shoes; no sources were screened")
    component = max(common, key=lambda label: np.count_nonzero(labels == label))
    clearance = cv2.distanceTransform((baseline[region] != 0).astype(np.uint8), cv2.DIST_L2, 5)
    anchors = {}
    for room in rooms:
        mask = (instances[region] == room_ids[room]) & (labels == component)
        point = np.unravel_index(np.argmax(np.where(mask, clearance, -1)), labels.shape)
        anchors[room] = np.asarray(point) + low
    room_regions = {
        room: (instances[region] == room_ids[room]) & (labels == component) for room in rooms
    }
    baseline_labels = labels

    candidates = sorted((session.paths.intervent_rawdata_root / "task-0022").glob("episode_*.hdf5"))
    if not candidates:
        raise FileNotFoundError("No task-0022 episode recordings found")
    accepted, rejected = [], []
    for path in candidates:
        try:
            source, bounds, positions, entry = read_layout(path, session, asset_root)
        except (KeyError, ValueError, OSError) as error:
            rejected.append({"hdf5": str(path.resolve()), "reason": "recording_schema", "detail": str(error)})
            continue
        entry_cell = (entry[:2][::-1] / resolution + size / 2).astype(int)
        local = entry_cell - low
        if np.any(local < 0) or np.any(local >= baseline_labels.shape) or baseline[tuple(entry_cell)] == 0:
            rejected.append({**source, "reason": "static_entry_blocked", "entry": entry.tolist()})
            continue
        if baseline_labels[tuple(local)] != component:
            rejected.append({**source, "reason": "static_entry_disconnected", "entry": entry.tolist()})
            continue
        occupancy = floor.copy()
        for shoe_low, shoe_high in bounds:
            rasterize_mesh_bounds(occupancy, shoe_low, shoe_high, resolution, size, 0.005, np.inf)
        grid, _ = erode_footprint(occupancy, -half_extent, half_extent, resolution)
        _, labels = cv2.connectedComponents((grid[region] != 0).astype(np.uint8), connectivity=4)
        entry_component = int(labels[tuple(local)])
        # A shoe may cover a room's reference point while leaving a valid detour.
        unreachable = [
            room for room, mask in room_regions.items()
            if entry_component == 0 or not np.any(mask & (labels == entry_component))
        ]
        if unreachable:
            rejected.append({
                **source, "reason": "blocked_entry" if entry_component == 0 else "disconnected_regions",
                "unreachable_rooms": unreachable, "shoe_positions": positions, "entry": entry.tolist(),
            })
        else:
            accepted.append(source)

    report = {
        "session_id": session.session_id, "scene_model": session.scene_model,
        "task_id": 22, "segment_id": segment_id, "map_resolution": resolution,
        "method": "static_initial_layout_region_connectivity",
        "floor_map": str(layout / "floor_trav_0.png"), "robot_half_extent_xy": half_extent.tolist(),
        "task_orders": session.composition["task_orders"],
        "rooms": rooms,
        "reference_room_anchors": {
            room: ((point[::-1] - size / 2) * resolution).tolist() for room, point in anchors.items()
        },
        "scope": (
            "Initial shoes only; static furniture/doors and fixed rectangular footprint. "
            "No live mesh, K/Q state, trim, or physical execution checks."
        ),
        "summary": {"available": len(candidates), "accepted": len(accepted), "rejected": len(rejected)},
        "accepted": accepted, "rejected": rejected,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"{session.session_id}: {len(accepted)}/{len(candidates)} passed; report: {output_path}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configs", nargs="+", help="mb000 / mb003 session YAML files")
    parser.add_argument("--output-dir", required=True, type=Path, help="New directory for session screening reports")
    parser.add_argument("--robot-half-extent", nargs=2, type=float, default=[0.35, 0.35], metavar=("X", "Y"),
                        help="Fixed rectangular footprint half extents in meters (default: 0.35 0.35)")
    args = parser.parse_args()
    half_extent = np.asarray(args.robot_half_extent)
    if not np.isfinite(half_extent).all() or np.any(half_extent <= 0):
        parser.error("Robot footprint half extents must be finite and positive")
    for config in args.configs:
        screen_session(Session.load(config), args.output_dir, half_extent)


if __name__ == "__main__":
    main()
