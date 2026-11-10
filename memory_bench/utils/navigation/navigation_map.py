import cv2
import numpy as np


def erode_footprint(grid, low_xy, high_xy, resolution):
    """Reserve an asymmetric world-aligned footprint around each robot base cell."""
    low = np.floor(np.asarray(low_xy)[::-1] / resolution).astype(int)
    high = np.ceil(np.asarray(high_xy)[::-1] / resolution).astype(int)
    kernel_low = np.minimum(low, 0)
    shape = np.maximum(high, 0) - kernel_low + 1
    kernel = np.zeros(tuple(shape), dtype=np.uint8)
    start, end = low - kernel_low, high - kernel_low + 1
    kernel[start[0]:end[0], start[1]:end[1]] = 1
    anchor = (-int(kernel_low[1]), -int(kernel_low[0]))
    eroded = cv2.erode(
        np.asarray(grid, dtype=np.uint8),
        kernel,
        anchor=anchor,
        borderType=cv2.BORDER_CONSTANT,
        borderValue=0,
    )
    return eroded, {"kernel_rows_columns": shape.tolist(), "anchor_column_row": list(anchor)}


def build_occupancy_map(scene, robot, floor):
    """Plan for the stowed robot's height-aware footprint in the current scene."""
    from omnigibson.utils.constants import GROUND_CATEGORIES

    traversability = scene.trav_map
    if traversability.trav_map_with_objects:
        raise ValueError("Use trav_map_with_objects=False before applying current-scene occupancy")
    floor_map = traversability.floor_map[floor].cpu().numpy()
    floor_height = float(traversability.floor_heights[floor])
    obstacles = []
    for obj in scene.objects:
        if obj is robot or obj.category in GROUND_CATEGORIES or obj.category == "ceilings":
            continue
        for link in obj.links.values():
            for mesh in link.collision_meshes.values():
                low, high = (value.detach().cpu().numpy() for value in mesh.aabb)
                if high[2] > floor_height + 0.005:
                    obstacles.append([low, high])
    obstacles = np.asarray(obstacles).reshape(-1, 2, 3)
    grid = np.full_like(floor_map, 255)
    position = robot.get_position_orientation()[0].detach().cpu().numpy()
    footprints = []
    for link in robot.links.values():
        for mesh in link.collision_meshes.values():
            low, high = (value.detach().cpu().numpy() for value in mesh.aabb)
            low_z = max(floor_height + 0.005, low[2])
            occupancy = floor_map.copy()
            overlapping = (obstacles[:, 1, 2] > low_z) & (obstacles[:, 0, 2] < high[2])
            for obstacle_low, obstacle_high in obstacles[overlapping]:
                rasterize_mesh_bounds(
                    occupancy, obstacle_low, obstacle_high, traversability.map_resolution,
                    traversability.map_size, low_z, high[2],
                )
            part_grid, footprint = erode_footprint(
                occupancy, low[:2] - position[:2], high[:2] - position[:2], traversability.map_resolution,
            )
            grid = np.minimum(grid, part_grid)
            footprints.append(footprint)
    return grid, footprints


def rasterize_mesh_bounds(grid, low_xyz, high_xyz, map_resolution, map_size, low_z, high_z):
    """Block one collision mesh only when its height overlaps the base component."""
    if high_xyz[2] <= low_z or low_xyz[2] >= high_z:
        return
    low = (np.asarray(low_xyz[:2])[::-1] / map_resolution + map_size / 2.0).astype(int)
    high = (np.asarray(high_xyz[:2])[::-1] / map_resolution + map_size / 2.0).astype(int)
    low = np.maximum(low, 0)
    high = np.minimum(high, np.asarray(grid.shape) - 1)
    if np.all(low <= high):
        grid[low[0]:high[0] + 1, low[1]:high[1] + 1] = 0
