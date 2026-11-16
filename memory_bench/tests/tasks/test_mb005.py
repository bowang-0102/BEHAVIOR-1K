from types import SimpleNamespace

import pytest
from omnigibson.object_states import OnTop

from memory_bench.tasks.mb005_return_bowl_and_cutting_board import TASK


@pytest.mark.parametrize("variant", TASK.variants)
@pytest.mark.parametrize("order", [("key_1", "key_2"), ("key_2", "key_1")])
def test_keys_work_in_either_order_and_preserve_other_bowl(variant, order):
    objects = {
        name: SimpleNamespace(initialized=True, on_top=None, states={})
        for name in TASK.object_usage[TASK.query_activity]["used"]
    }
    for obj in objects.values():
        obj.states[OnTop] = SimpleNamespace(get_value=lambda other, obj=obj: obj.on_top is other)

    bowl = objects["bowl.n.01_1"]
    other_bowl = objects["vessel.n.03_1"]
    board = objects["chopping_board.n.01_1"]
    bar = objects["countertop.n.01_1"]
    microwave = objects["microwave.n.02_1"]
    counter = objects["countertop.n.01_2"]
    bowl.on_top = microwave
    other_bowl.on_top = bar
    board.on_top = counter
    moved_objects = {"key_1": bowl, "key_2": board}

    assert not TASK.check_init("query", objects, variant)
    for segment in order:
        assert TASK.check_init(segment, objects, variant)
        assert not TASK.check_goal(segment, objects, variant)
        moved_objects[segment].on_top = bar
        assert TASK.check_goal(segment, objects, variant)

        other_bowl.on_top = counter
        assert not TASK.check_goal(segment, objects, variant)
        other_bowl.on_top = bar

    assert all(TASK.check_goal(segment, objects, variant) for segment in order)
    assert TASK.check_init("query", objects, variant)
    assert not TASK.check_goal("query", objects, variant)

    bowl.on_top = microwave
    assert not TASK.check_goal("query", objects, variant)
    board.on_top = counter
    assert TASK.check_goal("query", objects, variant)
    other_bowl.on_top = counter
    assert not TASK.check_goal("query", objects, variant)
