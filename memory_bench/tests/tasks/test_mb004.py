from types import SimpleNamespace

import pytest
from omnigibson.object_states import OnTop

from memory_bench.tasks.mb004_put_away_mug_and_tissue_box import TASK


@pytest.mark.parametrize("variant", TASK.variants)
@pytest.mark.parametrize("order", [("key_1", "key_2"), ("key_2", "key_1")])
def test_keys_work_in_either_order(variant, order):
    objects = {
        name: SimpleNamespace(initialized=True, on_top=None, states={})
        for name in TASK.object_usage[TASK.query_activity]["used"]
    }
    for obj in objects.values():
        obj.states[OnTop] = SimpleNamespace(get_value=lambda other, obj=obj: obj.on_top is other)

    mug = objects["mug.n.04_1"]
    tissue_box = objects["box__of__tissue.n.01_1"]
    table = objects["coffee_table.n.01_1"]
    mug.on_top = objects["bookcase.n.01_1"]
    tissue_box.on_top = objects["bookcase.n.01_3"]
    moved_objects = {"key_1": mug, "key_2": tissue_box}

    assert not TASK.check_init("query", objects, variant)
    for segment in order:
        assert TASK.check_init(segment, objects, variant)
        assert not TASK.check_goal(segment, objects, variant)
        moved_objects[segment].on_top = table
        assert TASK.check_goal(segment, objects, variant)

    # Completing either Key must preserve the other Key's result.
    assert all(TASK.check_goal(segment, objects, variant) for segment in order)
    assert TASK.check_init("query", objects, variant)
    assert not TASK.check_goal("query", objects, variant)

    mug.on_top = objects["bookcase.n.01_1"]
    assert not TASK.check_goal("query", objects, variant)
    tissue_box.on_top = objects["bookcase.n.01_3"]
    assert TASK.check_goal("query", objects, variant)
