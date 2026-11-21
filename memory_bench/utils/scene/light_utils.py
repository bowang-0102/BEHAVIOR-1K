"""Object-light synchronization adapted from omnigibson.eval.utils.light_utils."""

SELF_LIGHT_CATEGORIES = {"floor_lamp", "table_lamp", "flashlight"}


def _iter_descendant_prims(prim):
    for child in prim.GetChildren():
        yield child
        yield from _iter_descendant_prims(child)


def find_toggle_lights(scene):
    """Collect toggle states and their light prims in scene traversal order."""
    objects = [obj for obj in scene.objects if getattr(obj, "category", None) in SELF_LIGHT_CATEGORIES]
    if not objects:
        return []

    import omnigibson.lazy as lazy
    from omnigibson.object_states import ToggledOn

    lights = []
    for obj in objects:
        states = getattr(obj, "states", None)
        if states is None or ToggledOn not in states:
            continue
        prims = [
            lazy.pxr.UsdGeom.Imageable(prim)
            for prim in _iter_descendant_prims(obj.prim)
            if "Light" in prim.GetTypeName()
        ]
        lights.append((states[ToggledOn], prims))
    return lights


def sync_light_visibility_from_toggles(scene, *, lights=None):
    """Match each lamp's light prims to its current toggle; return whether visibility changed."""
    if lights is None:
        lights = find_toggle_lights(scene)
    if not lights:
        return False

    import omnigibson as og
    import omnigibson.lazy as lazy

    changed = False
    for toggle, prims in lights:
        visible = toggle.get_value()
        target = lazy.pxr.UsdGeom.Tokens.inherited if visible else lazy.pxr.UsdGeom.Tokens.invisible
        for imageable in prims:
            if imageable.GetVisibilityAttr().Get() == target:
                continue
            with og.sim.editing_usd():
                if visible:
                    imageable.MakeVisible()
                else:
                    imageable.MakeInvisible()
            changed = True
    return changed
