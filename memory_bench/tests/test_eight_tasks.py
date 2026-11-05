"""Check task semantics and the new preparation constraints without starting physics."""
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from memory_bench.collection.prepare_session import _shared_task_bindings
from memory_bench.tasks.resume_toolbox_packing import TASK as BOX_TASK
from memory_bench.tasks.retrieve_packed_flashlight import TASK as NESTED_TASK


def test_nested_update_requires_content_to_remain_in_box(monkeypatch):
    from omnigibson.utils import bddl_utils

    conditions = NESTED_TASK.variant_goal["left_A"]["key_2"]
    facts = {tuple(c) for c in conditions if c[0] != "not"}
    objects = {name: name for name in NESTED_TASK.object_usage[NESTED_TASK.query_activity]["used"]}
    monkeypatch.setattr(bddl_utils, "evaluate_bddl_predicate", lambda predicate, *names: (predicate.__name__.lower(), *names) in facts)
    assert NESTED_TASK.check_goal("key_2", objects, "left_A")
    facts.remove(("inside", "flashlight.n.01_1", "packing_box.n.02_1"))
    assert not NESTED_TASK.check_goal("key_2", objects, "left_A")
    assert {"flashlight.n.01_1", "packing_box.n.02_1"} <= set(
        NESTED_TASK.object_usage[NESTED_TASK.key_activities[1]]["write"]
    )


def test_same_model_boxes_keep_distinct_identities_across_activities(tmp_path):
    task = BOX_TASK
    support = "breakfast_table_xftrki_0"
    (tmp_path / f"{task.scene_model}_stable.json").write_text(json.dumps({
        "objects_info": {"init_info": {support: {}}},
    }))
    segments = dict(zip(["key_1", "query"], ({"activity": a} for a in (*task.key_activities, task.query_activity))))
    for segment, suffix in [("key_1", "first"), ("query", "second")]:
        boxes = {f"toolbox.n.01_{i}": f"toolbox_{suffix}_{i}" for i in (1, 2)}
        bindings = {"breakfast_table.n.01_1": support, **boxes}
        initializers = {name: {"args": {"category": "toolbox", "model": "redmtl", "scale": [1, 1, 1]}}
                        for name in boxes.values()}
        path = tmp_path / f"{task.scene_model}_task_{segments[segment]['activity']}_0_0_template.json"
        path.write_text(json.dumps({"metadata": {"task": {"inst_to_name": bindings}},
                                    "objects_info": {"init_info": initializers}}))
    session = SimpleNamespace(task=task, key_ids=["key_1"], query_id="query", segments=segments)
    bindings, _, sources = _shared_task_bindings(session, tmp_path)
    assert bindings["query"]["toolbox.n.01_1"] == "toolbox_first_1"
    assert bindings["query"]["toolbox.n.01_2"] == "toolbox_first_2"
    assert sources == {"toolbox_first_1": "key_1", "toolbox_first_2": "key_1"}
    session.task = replace(task, shared_object_roles=())
    with pytest.raises(ValueError, match="Ambiguous inserted model"):
        _shared_task_bindings(session, tmp_path)


def test_initial_state_opens_container_before_placement_and_closes_fully(monkeypatch):
    import omnigibson as og
    from omnigibson.object_states import Inside, Open

    events = []
    task = replace(BOX_TASK, initial_states={"A": [
        ["inside", "item", "box"], ["not", ["open", "box"]],
    ]}, initial_regions={}, top_surfaces=())
    objects = {
        "box": SimpleNamespace(states={Open: SimpleNamespace(set_value=lambda value, **kw: events.append(
            ("open", value, kw)) or True)}),
        "item": SimpleNamespace(states={Inside: SimpleNamespace(set_value=lambda support, value: events.append(
            ("inside", value)) or True)}),
    }
    monkeypatch.setattr(og, "sim", SimpleNamespace(step_physics=Mock()))
    task.prepare_initial_state(objects, "A")
    assert events == [("open", True, {"fully": True}), ("inside", True), ("open", False, {"fully": True})]


def test_region_uses_base_pose_not_lid_bounding_box():
    task = replace(BOX_TASK, top_surfaces=("support",))
    objects = {
        "support": SimpleNamespace(aabb=(np.array([0., 0., 0.]), np.array([1., 1., 1.]))),
        "box": SimpleNamespace(aabb=(np.array([0., 0., 1.]), np.array([1., 1., 2.])),
                               get_position_orientation=lambda: (np.array([.25, .25, 1.]), None)),
    }
    regions = {"box": ("support", .1, .1, .4, .4)}
    assert task._check_regions([["ontop", "box", "support"]], objects, regions)
    # An internal shelf can satisfy OnTop, but must not satisfy a top-surface task.
    objects["box"].aabb = (np.array([0., 0., .5]), np.array([1., 1., .8]))
    assert not task._check_regions([["ontop", "box", "support"]], objects, regions)
