import cv2
import numpy as np
import torch as th


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


def forward_navigation_map(scene, robot, floor):
    """Plan for the base; physical contact checks cover the full robot during execution."""
    grid = np.full_like(scene.trav_map.floor_map[floor].cpu().numpy(), 255)
    position, _ = robot.get_position_orientation()
    footprints = []
    for link in [robot.base_footprint_link, *robot.floor_touching_base_links]:
        for mesh in link.collision_meshes.values():
            low, high = mesh.aabb
            occupancy = current_floor_map(scene, robot, floor, float(low[2]), float(high[2])).cpu().numpy()
            part_grid, footprint = erode_footprint(
                occupancy, (low[:2] - position[:2]).cpu().numpy(),
                (high[:2] - position[:2]).cpu().numpy(), scene.trav_map.map_resolution,
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


def current_floor_map(scene, robot, floor, low_z, high_z):
    """Overlay live collision bounds within one base component's height range.

    The scene must load its object-free map. Physical execution checks full-body contacts
    and never freezes other objects.
    """
    from omnigibson.utils.constants import GROUND_CATEGORIES

    traversability = scene.trav_map
    if traversability.trav_map_with_objects:
        raise ValueError("Use trav_map_with_objects=False before applying current-scene occupancy")
    grid = traversability.floor_map[floor].cpu().numpy().copy()
    floor_height = float(traversability.floor_heights[floor])
    for obj in scene.objects:
        if obj is robot or obj.category in GROUND_CATEGORIES or obj.category == "ceilings":
            continue
        for link in obj.links.values():
            for mesh in link.collision_meshes.values():
                low, high = mesh.aabb
                rasterize_mesh_bounds(
                    grid,
                    low.cpu().numpy(),
                    high.cpu().numpy(),
                    traversability.map_resolution,
                    traversability.map_size,
                    max(floor_height + 0.005, low_z),
                    high_z,
                )
    return th.from_numpy(grid)
