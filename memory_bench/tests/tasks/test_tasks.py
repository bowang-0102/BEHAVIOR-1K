import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bddl import condition_evaluation
from omnigibson.object_states import Cooked, Inside, Open

from memory_bench.tasks import TASKS
from memory_bench.tasks.mb000_retrieve_bottle_of_milk import TASK

ITEM = "mug.n.04_1"
CABINET_1 = "cabinet.n.01_1"
CABINET_2 = "cabinet.n.01_2"
ALL_CLOSED = ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]]


@pytest.mark.parametrize("task", TASKS.values(), ids=TASKS.keys())
def test_registered_tasks_have_complete_conditions_and_instructions(task):
    required_entries = {f"key_{index}" for index in range(1, len(task.key_activities) + 1)} | {"query"}
    for conditions in (task.init, task.goal):
        assert set(conditions) == set(task.variants)
        for variant_id in task.variants:
            assert required_entries <= set(conditions[variant_id])
    for variant_id in task.variants:
        for activity in task.key_activities:
            assert task.instruction_for(activity, variant_id)
        assert task.instruction_for(task.query_activity, variant_id) == task.query_instruction
    assignments = task.sample_variants(150, 0)
    assert assignments == task.sample_variants(150, 0)
    assert len(assignments) == 150
    assert set(assignments.values()) == set(task.variants)


@pytest.fixture
def objects():
    cabinet_1 = SimpleNamespace(initialized=True, states={Open: SimpleNamespace(get_value=Mock(return_value=False))})
    cabinet_2 = SimpleNamespace(initialized=True, states={Open: SimpleNamespace(get_value=Mock(return_value=False))})
    item = SimpleNamespace(initialized=True, states={
        Cooked: SimpleNamespace(get_value=Mock(return_value=False)),
        Inside: SimpleNamespace(get_value=lambda other: other is cabinet_1),
    })
    return {ITEM: item, CABINET_1: cabinet_1, CABINET_2: cabinet_2}


@pytest.mark.parametrize("expression,expected", [
    (["not", ["open", CABINET_2]], True),
    (["or", ["open", CABINET_1], ["open", CABINET_2]], False),
    (["and", ["not", ["open", CABINET_1]], ["not", ["open", CABINET_2]]], True),
    (ALL_CLOSED, True),
    (["exists", ["?cabinet", "-", "cabinet.n.01"], ["open", "?cabinet"]], False),
])
def test_logical_expressions_read_live_states(objects, expression, expected):
    task = replace(TASK, goal={"A": {"query": [expression]}})
    assert task.check_goal("query", objects, "A") is expected
    objects[CABINET_2].states[Open].get_value.return_value = True
    assert task.check_goal("query", objects, "A") is not expected
    # Restoring the old state must also restore completion without recompiling.
    objects[CABINET_2].states[Open].get_value.return_value = False
    assert task.check_goal("query", objects, "A") is expected


def test_existing_cooked_predicate_needs_no_memory_bench_branch(objects):
    task = replace(TASK, goal={"A": {"query": [["cooked", ITEM]]}})
    assert not task.check_goal("query", objects, "A")
    objects[ITEM].states[Cooked].get_value.return_value = True
    assert task.check_goal("query", objects, "A")
    objects[ITEM].initialized = False
    assert not task.check_goal("query", objects, "A")


def test_binary_predicate_uses_current_object_bindings(objects):
    task = replace(TASK, goal={"A": {"query": [["inside", ITEM, CABINET_1]]}})
    assert task.check_goal("query", objects, "A")
    rebound = {**objects, CABINET_1: objects[CABINET_2]}
    assert not task.check_goal("query", rebound, "A")


def test_four_variants_select_their_own_initial_and_goal_conditions(objects):
    task = replace(
        TASK,
        variants={"A": {}, "B": {}, "C": {}, "D": {}},
        init={
            "A": {"key_1": [["open", CABINET_1]]},
            "B": {"key_1": [["not", ["open", CABINET_1]]]},
            "C": {"key_1": [ALL_CLOSED]},
            "D": {"key_1": [["exists", ["?cabinet", "-", "cabinet.n.01"], ["open", "?cabinet"]]]},
        },
        goal={
            "A": {"key_1": [["cooked", ITEM]]},
            "B": {"key_1": [["inside", ITEM, CABINET_1]]},
            "C": {"key_1": [["not", ["inside", ITEM, CABINET_1]]]},
            "D": {"key_1": [["not", ["cooked", ITEM]]]},
        },
    )
    for variant_id in task.variants:
        task.prepare_conditions({"key_1": objects}, variant_id)
    assert [
        (task.check_init("key_1", objects, variant_id), task.check_goal("key_1", objects, variant_id))
        for variant_id in task.variants
    ] == [(False, False), (True, True), (True, False), (False, True)]


def test_compilation_cache_preserves_live_evaluation_and_serialization(objects, monkeypatch):
    task = replace(
        TASK,
        init={"A": {"key_1": [["not", ["open", CABINET_1]]]}},
        goal={"A": {"key_1": [ALL_CLOSED]}},
    )
    snapshot = json.dumps(asdict(task), sort_keys=True)
    compile_spy = Mock(wraps=condition_evaluation.compile_state)
    monkeypatch.setattr(condition_evaluation, "compile_state", compile_spy)

    task.prepare_conditions({"key_1": objects}, "A")
    assert compile_spy.call_count == 2
    for _ in range(3):
        assert task.check_init("key_1", objects, "A")
        assert task.check_goal("key_1", objects, "A")
    assert compile_spy.call_count == 2

    objects[CABINET_2].states[Open].get_value.return_value = True
    assert not task.check_goal("key_1", objects, "A")
    objects[CABINET_2].states[Open].get_value.return_value = False
    assert task.check_goal("key_1", objects, "A")
    assert compile_spy.call_count == 2

    # A new logical scope must include the added cabinet in the quantifier.
    extra = SimpleNamespace(initialized=True, states={Open: SimpleNamespace(get_value=lambda: True)})
    expanded = {**objects, "cabinet.n.01_3": extra}
    assert not task.check_goal("key_1", expanded, "A")
    assert compile_spy.call_count == 3
    assert json.dumps(asdict(task), sort_keys=True) == snapshot


def test_unknown_predicate_is_rejected_during_preparation(objects):
    task = replace(TASK, init={"A": {"key_1": []}}, goal={"A": {"key_1": [["not_a_predicate", ITEM]]}})
    with pytest.raises(KeyError, match="not_a_predicate"):
        task.prepare_conditions({"key_1": objects}, "A")


def test_query_uses_fixed_instruction_even_when_key_instructions_are_missing():
    task = replace(
        TASK,
        query_instruction="Retrieve the mug.",
        key_instructions={},
    )
    assert task.instruction_for(task.query_activity, "A") == "Retrieve the mug."
    assert task.instruction_for(task.query_activity) == "Retrieve the mug."


@pytest.mark.parametrize("variant_id,instructions", [
    (None, TASK.key_instructions),
    ("A", {}),
    ("A", {"A": {}}),
])
def test_key_instruction_requires_variant_and_explicit_instruction(variant_id, instructions):
    task = replace(TASK, key_instructions=instructions)
    with pytest.raises(KeyError, match="No Key instruction declared"):
        task.instruction_for(task.key_activities[0], variant_id)

