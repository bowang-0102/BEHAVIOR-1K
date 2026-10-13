"""Conservative occupancy from collision links in the current unified scene."""

import torch as th


def current_floor_map(scene, robot, floor):
    """Overlay live collision-link bounds on the scene's structural floor map.

    The scene must load its object-free map. Bounds can reject narrow passages conservatively;
    physical execution still checks actual contacts and never freezes other objects.
    """
    from omnigibson.utils.constants import GROUND_CATEGORIES

    traversability = scene.trav_map
    if traversability.trav_map_with_objects:
        raise ValueError("Use trav_map_with_objects=False before applying current-scene occupancy")
    grid = traversability.floor_map[floor].clone()
    floor_height = float(traversability.floor_heights[floor])
    robot_height = float(robot.reset_joint_pos_aabb_extent[2])
    for obj in scene.objects:
        if obj is robot or obj.category in GROUND_CATEGORIES or obj.category == "ceilings":
            continue
        for link in obj.links.values():
            if not link.collision_meshes:
                continue
            low, high = link.aabb
            if float(high[2]) <= floor_height + 0.005 or float(low[2]) >= floor_height + robot_height:
                continue
            corners = th.stack([low[:2], high[:2]])
            mapped = traversability.world_to_map(corners).long()
            lo = mapped.min(dim=0).values.clamp(min=0)
            hi = mapped.max(dim=0).values
            if lo[0] >= grid.shape[0] or lo[1] >= grid.shape[1] or hi[0] < 0 or hi[1] < 0:
                continue
            hi = th.minimum(hi, th.tensor(grid.shape) - 1)
            grid[int(lo[0]) : int(hi[0]) + 1, int(lo[1]) : int(hi[1]) + 1] = 0
    return grid
