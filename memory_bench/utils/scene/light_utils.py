"""Object-light synchronization adapted from omnigibson.eval.utils.light_utils."""

SELF_LIGHT_CATEGORIES = {"floor_lamp", "table_lamp", "flashlight"}


def _iter_descendant_prims(prim):
    for child in prim.GetChildren():
        yield child
        yield from _iter_descendant_prims(child)


def sync_light_visibility_from_toggles(scene):
    """Match each lamp's light prims to its current toggle; return whether visibility changed.

    Room lighting is independent of these portable task objects. Unlike the eval synchronizer,
    this also restores an initially-off lamp correctly after resets or arbitrary replay seeks.
    """
    objects = [obj for obj in scene.objects if getattr(obj, "category", None) in SELF_LIGHT_CATEGORIES]
    if not objects:
        return False

    import omnigibson as og
    import omnigibson.lazy as lazy
    from omnigibson.object_states import ToggledOn

    changed = False
    for obj in objects:
        states = getattr(obj, "states", None)
        if states is None or ToggledOn not in states:
            continue
        visible = states[ToggledOn].get_value()
        target = lazy.pxr.UsdGeom.Tokens.inherited if visible else lazy.pxr.UsdGeom.Tokens.invisible
        for prim in _iter_descendant_prims(obj.prim):
            if "Light" not in prim.GetTypeName():
                continue
            imageable = lazy.pxr.UsdGeom.Imageable(prim)
            if imageable.GetVisibilityAttr().Get() == target:
                continue
            with og.sim.editing_usd():
                if visible:
                    imageable.MakeVisible()
                else:
                    imageable.MakeInvisible()
            changed = True
    return changed
