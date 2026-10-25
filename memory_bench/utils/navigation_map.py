import math

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


def forward_navigation_map(scene, robot, floor, position_tolerance):
    """Use the existing grid route as a candidate, checking the live outline at each heading."""
    from omnigibson.utils import transform_utils as T

    grid = current_floor_map(scene, robot, floor).cpu().numpy()
    position, orientation = robot.get_position_orientation()
    yaw = float(T.quat2euler(orientation)[2])
    points = (robot.collision_points_world[:, :2] - position[:2]).detach().cpu().numpy()
    rotation = np.asarray([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
    outline = cv2.convexHull((points @ rotation).astype(np.float32)).reshape(-1, 2).astype(float)
    low = points.min(axis=0) - position_tolerance
    high = points.max(axis=0) + position_tolerance
    planned, footprint = erode_footprint(grid, low, high, scene.trav_map.map_resolution)
    footprint.update(outline_xy=outline.tolist(), tracking_margin=position_tolerance)
    return planned, grid, footprint


def footprint_clear(grid, traversability, outline, xy, yaw):
    """Check a convex robot outline against occupied grid squares, including their edges."""
    rotation = np.asarray([[math.cos(yaw), -math.sin(yaw)], [math.sin(yaw), math.cos(yaw)]])
    polygon = outline @ rotation.T + xy
    cells = polygon[:, ::-1] / traversability.map_resolution + traversability.map_size / 2
    low = np.floor(cells.min(axis=0)).astype(int)
    high = np.floor(cells.max(axis=0)).astype(int)
    if np.any(low < 0) or np.any(high >= grid.shape):
        return False
    blocked = np.argwhere(grid[low[0]:high[0] + 1, low[1]:high[1] + 1] == 0) + low
    if not len(blocked):
        return True
    centers = (blocked[:, ::-1] + 0.5 - traversability.map_size / 2) * traversability.map_resolution
    edges = np.roll(polygon, -1, axis=0) - polygon
    axes = np.vstack((np.column_stack((-edges[:, 1], edges[:, 0])), np.eye(2)))
    polygon_projection = polygon @ axes.T
    cell_projection = centers @ axes.T
    radius = traversability.map_resolution / 2 * np.abs(axes).sum(axis=1)
    overlap = (
        (cell_projection + radius >= polygon_projection.min(axis=0))
        & (cell_projection - radius <= polygon_projection.max(axis=0))
    )
    return not bool(np.any(np.all(overlap, axis=1)))


def braking_motion_clear(
    grid, traversability, outline, xy, yaw, speed, rotation, dt, acceleration, angular_acceleration,
    velocity_yaw=None,
):
    """Predict one commanded step followed by braking, including the rotating body's corners."""
    radius = float(np.linalg.norm(outline, axis=1).max())
    horizon = dt + max(speed / acceleration, abs(rotation) / angular_acceleration)
    sample_dt = min(dt, traversability.map_resolution / (2 * max(speed + radius * abs(rotation), 1e-8)))
    count = max(1, math.ceil(horizon / sample_dt))
    sample_dt = horizon / count
    point = np.asarray(xy, dtype=float).copy()
    if not footprint_clear(grid, traversability, outline, point, yaw):
        return False
    for index in range(count):
        braking_time = max(0.0, (index + 0.5) * sample_dt - dt)
        velocity = max(0.0, speed - acceleration * braking_time)
        omega = math.copysign(max(0.0, abs(rotation) - angular_acceleration * braking_time), rotation)
        heading = yaw + omega * sample_dt / 2 if velocity_yaw is None else velocity_yaw
        point += velocity * sample_dt * np.asarray([math.cos(heading), math.sin(heading)])
        yaw += omega * sample_dt
        if not footprint_clear(grid, traversability, outline, point, yaw):
            return False
    return True


def rotation_clear(grid, traversability, outline, xy, yaw, target_yaw):
    """Check the full shortest turn before starting an in-place rotation."""
    difference = (target_yaw - yaw + math.pi) % (2 * math.pi) - math.pi
    radius = float(np.linalg.norm(outline, axis=1).max())
    count = max(1, math.ceil(abs(difference) * radius / (traversability.map_resolution / 2)))
    return all(
        footprint_clear(grid, traversability, outline, xy, angle)
        for angle in np.linspace(yaw, yaw + difference, count + 1)
    )


def forward_route_clear(grid, traversability, outline, route, yaw):
    """Validate the candidate straight runs and corner turns without an orientation search."""
    for start, end in zip(route, route[1:]):
        delta = end - start
        heading = math.atan2(delta[1], delta[0])
        if not rotation_clear(grid, traversability, outline, start, yaw, heading):
            return False
        count = max(1, math.ceil(float(np.linalg.norm(delta)) / (traversability.map_resolution / 2)))
        if not all(
            footprint_clear(grid, traversability, outline, xy, heading)
            for xy in np.linspace(start, end, count + 1)
        ):
            return False
        yaw = heading
    return True


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
