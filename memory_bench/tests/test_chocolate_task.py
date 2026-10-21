"""The lower-floor task keeps one hidden location across nearby interventions."""

from collections import Counter
from dataclasses import replace
import sys
from types import SimpleNamespace

import pytest
from bddl.activity import Conditions, get_goal_conditions, get_initial_conditions, get_object_scope
from bddl.condition_evaluation import evaluate_state
from bddl.object_taxonomy import ObjectTaxonomy
from bddl.predicates import Inside, OnTop, Open

from memory_bench.tasks import get_task
from memory_bench.tasks.retrieve_chocolate_from_top_cabinet import TASK


def bindings_for_case(case_id):
    return TASK.bind_variant(
        {
            "chocolate_bar.n.01_1": "chocolate_bar_73",
            "cabinet.n.01_1": "top_cabinet_tynnnw_0",
            "cabinet.n.01_2": "top_cabinet_tynnnw_1",
            "countertop.n.01_1": "bar_egwapq_0",
            "floor.n.01_1": "kitchen_floor",
            "agent.n.01_1": "robot",
        },
        case_id,
    )


@pytest.fixture
def world(monkeypatch):
    inside, ontop, opened = object(), object(), object()
    monkeypatch.setitem(
        sys.modules, "omnigibson.object_states", SimpleNamespace(Inside=inside, OnTop=ontop, Open=opened)
    )

    def make_world(case_id, placement, open_cabinets=()):
        bindings = bindings_for_case(case_id)
        facts = {("ontop", "robot", "kitchen_floor")}
        relation, destination = {
            "countertop": ("ontop", "countertop.n.01_1"),
            "target": ("inside", "cabinet.n.01_1"),
            "other": ("inside", "cabinet.n.01_2"),
        }[placement]
        facts.add((relation, bindings["chocolate_bar.n.01_1"], bindings[destination]))
        facts.update(("open", bindings[instance]) for instance in open_cabinets)
        objects = {instance: SimpleNamespace(name=name) for instance, name in bindings.items()}
        for obj in objects.values():
            obj.states = {
                inside: SimpleNamespace(get_value=lambda other, obj=obj: ("inside", obj.name, other.name) in facts),
                ontop: SimpleNamespace(get_value=lambda other, obj=obj: ("ontop", obj.name, other.name) in facts),
                opened: SimpleNamespace(get_value=lambda obj=obj: ("open", obj.name) in facts),
            }

        def evaluate(predicate, *instances):
            token = {Inside: "inside", OnTop: "ontop", Open: "open"}[predicate]
            return (token, *(bindings[instance] for instance in instances)) in facts

        return objects, facts, evaluate

    return make_world


@pytest.mark.parametrize("case_id", ["A", "B"])
@pytest.mark.parametrize(
    "placement,open_cabinets,expected",
    [
        ("countertop", (), (True, False, False, True)),
        ("target", (), (False, True, True, False)),
        ("other", (), (False, False, False, False)),
        ("target", ("cabinet.n.01_1",), (False, False, False, False)),
        ("target", ("cabinet.n.01_2",), (False, False, False, False)),
        ("countertop", ("cabinet.n.01_1",), (False, False, False, False)),
    ],
)
def test_bddl_and_live_checks_agree_on_the_hidden_location(world, case_id, placement, open_cabinets, expected):
    objects, _, evaluate = world(case_id, placement, open_cabinets)
    live = (
        TASK.check_entry("key_1", objects, case_id),
        TASK.check_segment("key_1", "key_task", objects, case_id),
        TASK.check_entry("query", objects, case_id),
        TASK.check_segment("query", "query_task", objects, case_id),
    )
    bddl = []
    for activity in (*TASK.key_activities, TASK.query_activity):
        conditions = Conditions(activity, 0, "behavior-1k")
        scope = get_object_scope(conditions)
        initial = get_initial_conditions(conditions, scope)
        goal = get_goal_conditions(conditions, scope)
        bddl.extend((evaluate_state(initial, evaluate)[0], evaluate_state(goal, evaluate)[0]))
    assert live == tuple(bddl) == expected


@pytest.mark.parametrize("case_id", ["A", "B"])
def test_only_opening_the_other_physical_cabinet_is_forbidden(world, case_id):
    objects, facts, _ = world(case_id, "target")
    events, previous = TASK.query_events(objects, case_id=case_id)
    assert events == []

    facts.add(("open", objects["cabinet.n.01_1"].name))
    events, previous = TASK.query_events(objects, previous, case_id)
    assert events == []

    facts.add(("open", objects["cabinet.n.01_2"].name))
    events, previous = TASK.query_events(objects, previous, case_id)
    assert events == [
        {
            "type": "wrong_cabinet_opened",
            "bddl_instance": "cabinet.n.01_2",
            "object_id": objects["cabinet.n.01_2"].name,
        }
    ]
    assert TASK.query_events(objects, previous, case_id)[0] == []


def test_balanced_cases_change_only_location_and_key_instruction():
    assert get_task(TASK.task_name) is TASK
    assert Counter(TASK.sample_cases(150, 0).values()) == {"A": 75, "B": 75}
    cases = [bindings_for_case(case_id) for case_id in TASK.variants]
    assert cases[0]["cabinet.n.01_1"] != cases[1]["cabinet.n.01_1"]
    assert set(cases[0].values()) == set(cases[1].values())
    assert cases[0]["chocolate_bar.n.01_1"] == cases[1]["chocolate_bar.n.01_1"]
    assert cases[0]["countertop.n.01_1"] == cases[1]["countertop.n.01_1"]
    key_instructions = {TASK.instruction_for(TASK.key_activities[0], case_id) for case_id in TASK.variants}
    query_instructions = {TASK.instruction_for(TASK.query_activity, case_id) for case_id in TASK.variants}
    assert len(key_instructions) == 2
    assert query_instructions == {TASK.query_instruction}


def test_query_uses_query_instruction_instead_of_duplicate_or_key_text():
    task = replace(
        TASK,
        query_instruction="Retrieve the chocolate.",
        segment_instructions={**TASK.segment_instructions, TASK.query_activity: "Unused duplicate."},
        key_instructions={"A": {**TASK.key_instructions["A"], TASK.query_activity: "Unused Key text."}},
    )
    assert task.instruction_for(task.query_activity, "A") == "Retrieve the chocolate."
    assert task.instruction_for(task.query_activity) == "Retrieve the chocolate."
    assert task.instruction_for(task.key_activities[0], "A") == TASK.key_instructions["A"][task.key_activities[0]]


def test_sampling_constraints_use_valid_bddl_categories():
    taxonomy = ObjectTaxonomy()
    for activity in (*TASK.key_activities, TASK.query_activity):
        conditions = Conditions(activity, 0, "behavior-1k")
        assert all(taxonomy.is_valid_synset(synset) for synset in conditions.parsed_objects)
        for synset, categories in TASK.whitelist_for(activity).items():
            assert synset in conditions.parsed_objects
            assert set(categories) <= set(taxonomy.get_subtree_categories(synset))
