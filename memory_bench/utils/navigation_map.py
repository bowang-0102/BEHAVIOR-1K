"""Conservative occupancy from collision links in the current unified scene."""

import cv2
import numpy as np
import torch as th


def erode_footprint(grid, low_xy, high_xy, resolution):
    """Reserve an asymmetric world-aligned footprint around each robot base cell."""
    low = np.floor(np.asarray(low_xy)[::-1] / resolution).astype(int)
    high = np.ceil(np.asarray(high_xy)[::-1] / resolution).astype(int)
    shape = high - low + 1
    anchor = (-int(low[1]), -int(low[0]))
    eroded = cv2.erode(
        np.asarray(grid, dtype=np.uint8),
        np.ones(tuple(shape), dtype=np.uint8),
        anchor=anchor,
        borderType=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return eroded, {"kernel_rows_columns": shape.tolist(), "anchor_column_row": list(anchor)}


def fixed_heading_floor_map(scene, robot, floor, position_tolerance):
    """Reserve the live stowed footprint plus tracking error for fixed-yaw translation."""
    grid = current_floor_map(scene, robot, floor)
    position, _ = robot.get_position_orientation()
    low, high = robot.aabb
    low = (low[:2] - position[:2]).detach().cpu().numpy() - position_tolerance
    high = (high[:2] - position[:2]).detach().cpu().numpy() + position_tolerance
    eroded, footprint = erode_footprint(grid.cpu().numpy(), low, high, scene.trav_map.map_resolution)
    footprint.update(
        low_relative_xy=low.tolist(),
        high_relative_xy=high.tolist(),
        tracking_margin=position_tolerance,
    )
    return th.from_numpy(eroded), footprint


def rasterize_mesh_bounds(grid, low_xyz, high_xyz, map_resolution, map_size, low_z, high_z):
    """Block one collision mesh's bounds, preserving gaps between separate meshes."""
    if high_xyz[2] <= low_z or low_xyz[2] >= high_z:
        return
    low = (np.asarray(low_xyz[:2])[::-1] / map_resolution + map_size / 2.0).astype(int)
    high = (np.asarray(high_xyz[:2])[::-1] / map_resolution + map_size / 2.0).astype(int)
    low = np.maximum(low, 0)
    high = np.minimum(high, np.asarray(grid.shape) - 1)
    if np.all(low <= high):
        grid[low[0] : high[0] + 1, low[1] : high[1] + 1] = 0


def current_floor_map(scene, robot, floor):
    """Overlay live collision-link bounds on the scene's structural floor map.

    The scene must load its object-free map. Bounds can reject narrow passages conservatively;
    physical execution still checks actual contacts and never freezes other objects.
    """
    from omnigibson.utils.constants import GROUND_CATEGORIES

    traversability = scene.trav_map
    if traversability.trav_map_with_objects:
        raise ValueError("Use trav_map_with_objects=False before applying current-scene occupancy")
    grid = traversability.floor_map[floor].cpu().numpy().copy()
    floor_height = float(traversability.floor_heights[floor])
    robot_height = float(robot.reset_joint_pos_aabb_extent[2])
    for obj in scene.objects:
        if obj is robot or obj.category in GROUND_CATEGORIES or obj.category == "ceilings":
            continue
        for link in obj.links.values():
            if not link.collision_meshes:
                continue
            for mesh in link.collision_meshes.values():
                low, high = mesh.aabb
                rasterize_mesh_bounds(
                    grid,
                    low.cpu().numpy(),
                    high.cpu().numpy(),
                    traversability.map_resolution,
                    traversability.map_size,
                    floor_height + 0.005,
                    floor_height + robot_height,
                )
    return th.from_numpy(grid)
