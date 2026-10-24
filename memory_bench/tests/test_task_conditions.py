from dataclasses import asdict, replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bddl import condition_evaluation
from omnigibson.object_states import Cooked, Inside, Open

from memory_bench.tasks.retrieve_chocolate_from_top_cabinet import TASK


ITEM = "chocolate_bar.n.01_1"
CABINET_1 = "cabinet.n.01_1"
CABINET_2 = "cabinet.n.01_2"
ALL_CLOSED = ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]]


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
    task = replace(TASK, goal={"query": [expression]})
    assert task.check_goal("query", objects, "A") is expected
    objects[CABINET_2].states[Open].get_value.return_value = True
    assert task.check_goal("query", objects, "A") is not expected
    # Restoring the old state must also restore completion without recompiling.
    objects[CABINET_2].states[Open].get_value.return_value = False
    assert task.check_goal("query", objects, "A") is expected


def test_existing_cooked_predicate_needs_no_memory_bench_branch(objects):
    task = replace(TASK, goal={"query": [["cooked", ITEM]]})
    assert not task.check_goal("query", objects, "A")
    objects[ITEM].states[Cooked].get_value.return_value = True
    assert task.check_goal("query", objects, "A")
    objects[ITEM].initialized = False
    assert not task.check_goal("query", objects, "A")


def test_binary_predicate_uses_current_object_bindings(objects):
    task = replace(TASK, goal={"query": [["inside", ITEM, CABINET_1]]})
    assert task.check_goal("query", objects, "A")
    rebound = {**objects, CABINET_1: objects[CABINET_2]}
    assert not task.check_goal("query", rebound, "A")


def test_variant_overrides_and_default_conditions(objects):
    task = replace(
        TASK,
        init={"key_1": [["not", ["open", CABINET_1]]]},
        goal={"key_1": [["inside", ITEM, CABINET_1]]},
        variant_init={"A": {"key_1": [["open", CABINET_1]]}},
        variant_goal={"A": {"key_1": [["cooked", ITEM]]}},
    )
    assert not task.check_init("key_1", objects, "A")
    assert task.check_init("key_1", objects, "B")
    assert not task.check_goal("key_1", objects, "A")
    assert task.check_goal("key_1", objects, "B")


def test_compilation_cache_preserves_live_evaluation_and_serialization(objects, monkeypatch):
    task = replace(TASK, init={"key_1": [["not", ["open", CABINET_1]]]}, goal={"key_1": [ALL_CLOSED]})
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
    task = replace(TASK, init={"key_1": []}, goal={"key_1": [["not_a_predicate", ITEM]]})
    with pytest.raises(KeyError, match="not_a_predicate"):
        task.prepare_conditions({"key_1": objects}, "A")
