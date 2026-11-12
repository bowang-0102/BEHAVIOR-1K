from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bddl.condition_evaluation import compile_state, evaluate_state
from bddl.parsing import parse_problem
from omnigibson.object_states import OnTop, ToggledOn
from omnigibson.utils.bddl_utils import evaluate_bddl_predicate

from memory_bench.tasks.mb002_place_reading_lamp_by_previous_state import TASK as LAMP
from memory_bench.tasks.mb003_place_radio_by_previous_state import TASK as RADIO


@pytest.fixture(params=[
    (LAMP, "table_lamp.n.01_1", "desk.n.01_1", "bed.n.01_1", "desk.n.01_1", "bookcase.n.01_1"),
    (RADIO, "radio_receiver.n.01_1", "coffee_table.n.01_1", "sofa.n.01_1", "cabinet.n.01_1", "coffee_table.n.01_1"),
], ids=["reading_lamp", "radio"])
def case(request):
    return request.param


@pytest.fixture
def objects(case):
    task, target, initial, *_ = case
    objects = {
        role: SimpleNamespace(name=name, initialized=True, on_top=None, toggled_on=False, states={})
        for role, name in task.variants["A"].items()
    }
    for obj in objects.values():
        def set_toggle(value, obj=obj):
            obj.toggled_on = value
            return True

        obj.states = {
            OnTop: SimpleNamespace(get_value=lambda other, obj=obj: obj.on_top is other),
            ToggledOn: SimpleNamespace(get_value=lambda obj=obj: obj.toggled_on, set_value=set_toggle),
        }
    objects[target].on_top = objects[initial]
    return objects


@pytest.mark.parametrize("variant", ["A", "B"])
def test_history_changes_the_required_action_without_changing_identity(case, objects, variant):
    task, target, initial, temporary, destination_a, destination_b = case
    assert task.variants["A"] == task.variants["B"]
    assert task.init["A"]["query"] == task.init["B"]["query"]
    assert task.instruction_for(task.query_activity, "A") == task.instruction_for(task.query_activity, "B")
    obj = objects[target]
    # Preparation must establish either historical state on the same sampled support.
    obj.toggled_on = variant != "A"
    task.sample_initial_states("key_1", objects, variant)
    assert obj.toggled_on is (variant == "A")
    assert obj.on_top is objects[initial]
    assert task.check_init("key_1", objects, variant)
    assert not task.check_init("key_1", objects, "B" if variant == "A" else "A")
    assert not task.check_goal("key_1", objects, variant)

    obj.on_top = objects[temporary]
    obj.toggled_on = False
    assert task.check_goal("key_1", objects, variant)
    assert task.check_init("query", objects, variant)
    assert not task.check_goal("query", objects, variant)

    right, wrong = (destination_a, destination_b) if variant == "A" else (destination_b, destination_a)
    obj.on_top = objects[wrong]
    obj.toggled_on = variant == "A"
    assert not task.check_goal("query", objects, variant)
    obj.on_top = objects[right]
    obj.toggled_on = variant != "A"
    assert not task.check_goal("query", objects, variant)
    obj.toggled_on = variant == "A"
    assert task.check_goal("query", objects, variant)
    assert not task.check_goal("query", objects, "B" if variant == "A" else "A")
    assert task.object_usage[task.query_activity]["write"] == (target,)


def test_sampler_templates_allow_both_histories_but_runtime_requires_the_assigned_branch(case, objects):
    task, target, _, temporary, destination_a, destination_b = case
    obj = objects[target]

    def evaluate(predicate, *names):
        return evaluate_bddl_predicate(predicate, *(objects[name] for name in names))

    for segment, activity in (("key_1", task.key_activities[0]), ("query", task.query_activity)):
        _, object_map, initial, goal = parse_problem(activity, 0, "behavior-1k")
        initial = [condition for condition in initial
                   if condition[0] != "inroom" and condition[1] != "agent.n.01_1"]
        if segment == "key_1":
            for variant in task.variants:
                assert initial == [condition for condition in task.init[variant][segment] if condition[0] == "ontop"]
        else:
            assert initial == task.init["A"][segment] == task.init["B"][segment]
        compiled_initial = compile_state(initial, scope=set(objects), object_map=object_map)
        compiled_goal = compile_state(goal, scope=set(objects), object_map=object_map)
        for variant, destination in (("A", destination_a), ("B", destination_b)):
            if segment == "key_1":
                obj.on_top = objects[case[2]]
                obj.toggled_on = variant == "A"
            else:
                obj.on_top = objects[temporary]
                obj.toggled_on = False
            assert evaluate_state(compiled_initial, evaluate)[0]
            obj.on_top = objects[temporary if segment == "key_1" else destination]
            obj.toggled_on = segment == "query" and variant == "A"
            assert evaluate_state(compiled_goal, evaluate)[0]
            assert task.check_goal(segment, objects, variant)


def test_initial_state_sampling_preserves_geometry_and_propagates_native_failure(case, objects):
    task, target, _, temporary, *_ = case
    obj = objects[target]
    obj.on_top = objects[temporary]
    task.sample_initial_states("key_1", objects, "A")
    assert obj.on_top is objects[temporary]
    assert not task.check_init("key_1", objects, "A")
    obj.states[ToggledOn].set_value = Mock(return_value=False)
    with pytest.raises(RuntimeError, match="Could not initialize toggled_on"):
        task.sample_initial_states("key_1", objects, "B")
