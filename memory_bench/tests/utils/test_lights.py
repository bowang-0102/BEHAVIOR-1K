from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import omnigibson as og
import omnigibson.lazy as lazy
import pytest
from omnigibson.object_states import ToggledOn

from memory_bench.utils.recording.wrappers import LightSyncWrapper
from memory_bench.utils.scene.light_utils import sync_light_visibility_from_toggles


@pytest.fixture
def light_scene(monkeypatch):
    class Prim:
        def __init__(self, kind, *children):
            self.kind, self.children = kind, children
            self.visibility = "inherited"

        def GetChildren(self):
            return self.children

        def GetTypeName(self):
            return self.kind

        def GetVisibilityAttr(self):
            return SimpleNamespace(Get=lambda: self.visibility)

        def MakeVisible(self):
            self.visibility = "inherited"

        def MakeInvisible(self):
            self.visibility = "invisible"

    bulb = Prim("SphereLight")
    mesh = Prim("Mesh")
    root = Prim("Xform", Prim("Xform", bulb, mesh))
    toggle = SimpleNamespace(value=False)
    obj = SimpleNamespace(category="flashlight", prim=root, states={
        ToggledOn: SimpleNamespace(get_value=lambda: toggle.value),
    })
    sim = SimpleNamespace(editing_usd=Mock(side_effect=nullcontext), render=Mock(), step=Mock())
    monkeypatch.setattr(og, "sim", sim)
    monkeypatch.setitem(vars(lazy), "pxr", SimpleNamespace(UsdGeom=SimpleNamespace(
        Tokens=SimpleNamespace(inherited="inherited", invisible="invisible"), Imageable=lambda prim: prim,
    )))
    return SimpleNamespace(objects=[obj]), toggle, bulb, mesh, sim


@pytest.mark.parametrize("category", ["flashlight", "table_lamp", "floor_lamp"])
def test_lights_follow_absolute_state_after_loads_and_repeated_sync(light_scene, category):
    scene, toggle, bulb, mesh, sim = light_scene
    scene.objects[0].category = category
    for value, changed in [(False, True), (False, False), (True, True), (False, True), (True, True)]:
        toggle.value = value
        assert sync_light_visibility_from_toggles(scene) == changed
        assert bulb.visibility == ("inherited" if value else "invisible")
        assert mesh.visibility == "inherited"
    assert sim.editing_usd.call_count == 4
    sim.render.assert_not_called()
    sim.step.assert_not_called()


def test_portable_lights_do_not_change_room_switches_or_objects_without_toggle(light_scene):
    scene, toggle, bulb, mesh, sim = light_scene
    scene.objects[0].category = "electric_switch"
    assert not sync_light_visibility_from_toggles(scene)
    scene.objects[0].category = "flashlight"
    scene.objects[0].states = {}
    assert not sync_light_visibility_from_toggles(scene)
    assert bulb.visibility == "inherited"
    sim.editing_usd.assert_not_called()


def test_collection_refreshes_observations_after_light_changes_and_reset(light_scene):
    scene, toggle, bulb, mesh, sim = light_scene
    env = SimpleNamespace(
        scene=scene,
        step=Mock(return_value=([{"rgb": "stale"}], "reward", "terminated", "truncated", [{}])),
        reset=Mock(return_value=([{"rgb": "stale"}], {"obs_info": [{}], "reset": True})),
        get_obs=Mock(side_effect=lambda: ([{"rgb": bulb.visibility}], [{"camera": "updated"}])),
    )
    wrapper = LightSyncWrapper(env)
    result = wrapper.step("action")
    assert result == ([{"rgb": "invisible"}], "reward", "terminated", "truncated", [
        {"obs_info": {"camera": "updated"}},
    ])
    assert sim.render.call_count == 3
    wrapper.step("action")
    assert sim.render.call_count == 3
    toggle.value = True
    assert wrapper.step("action")[0] == [{"rgb": "inherited"}]
    toggle.value = False
    assert wrapper.reset() == ([{"rgb": "invisible"}], {"obs_info": [{"camera": "updated"}], "reset": True})
    assert sim.render.call_count == 9
    assert env.step.call_count == 3
    sim.step.assert_not_called()
