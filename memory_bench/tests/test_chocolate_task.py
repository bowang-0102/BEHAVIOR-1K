"""The lower-floor task keeps one hidden location across nearby interventions."""

from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest
from bddl.activity import Conditions, get_goal_conditions, get_initial_conditions, get_object_scope
from bddl.condition_evaluation import evaluate_state
from bddl.object_taxonomy import ObjectTaxonomy
from bddl.predicates import Inside, OnTop, Open
from omnigibson.object_states import Inside as InsideState, OnTop as OnTopState, Open as OpenState

from memory_bench.tasks import get_task
from memory_bench.tasks.retrieve_chocolate_from_top_cabinet import TASK


def bindings_for_variant(variant_id):
    return TASK.bind_variant(
        {
            "chocolate_bar.n.01_1": "chocolate_bar_73",
            "cabinet.n.01_1": "top_cabinet_tynnnw_0",
            "cabinet.n.01_2": "top_cabinet_tynnnw_1",
            "countertop.n.01_1": "bar_egwapq_0",
            "floor.n.01_1": "kitchen_floor",
            "agent.n.01_1": "robot",
        },
        variant_id,
    )


@pytest.fixture
def world():
    def make_world(variant_id, placement, open_cabinets=()):
        bindings = bindings_for_variant(variant_id)
        facts = {("ontop", "robot", "kitchen_floor")}
        relation, destination = {
            "countertop": ("ontop", "countertop.n.01_1"),
            "target": ("inside", "cabinet.n.01_1"),
            "other": ("inside", "cabinet.n.01_2"),
        }[placement]
        facts.add((relation, bindings["chocolate_bar.n.01_1"], bindings[destination]))
        facts.update(("open", bindings[instance]) for instance in open_cabinets)
        objects = {instance: SimpleNamespace(name=name, initialized=True) for instance, name in bindings.items()}
        for obj in objects.values():
            obj.states = {
                InsideState: SimpleNamespace(
                    get_value=lambda other, obj=obj: ("inside", obj.name, other.name) in facts
                ),
                OnTopState: SimpleNamespace(get_value=lambda other, obj=obj: ("ontop", obj.name, other.name) in facts),
                OpenState: SimpleNamespace(get_value=lambda obj=obj: ("open", obj.name) in facts),
            }

        def evaluate(predicate, *instances):
            token = {Inside: "inside", OnTop: "ontop", Open: "open"}[predicate]
            return (token, *(bindings[instance] for instance in instances)) in facts

        return objects, facts, evaluate

    return make_world


@pytest.mark.parametrize("variant_id", ["A", "B"])
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
def test_bddl_and_live_checks_agree_on_the_hidden_location(world, variant_id, placement, open_cabinets, expected):
    objects, _, evaluate = world(variant_id, placement, open_cabinets)
    live = (
        TASK.check_init("key_1", objects, variant_id),
        TASK.check_goal("key_1", objects, variant_id),
        TASK.check_init("query", objects, variant_id),
        TASK.check_goal("query", objects, variant_id),
    )
    bddl = []
    for activity in (*TASK.key_activities, TASK.query_activity):
        conditions = Conditions(activity, 0, "behavior-1k")
        scope = get_object_scope(conditions)
        initial = get_initial_conditions(conditions, scope)
        goal = get_goal_conditions(conditions, scope)
        bddl.extend((evaluate_state(initial, evaluate)[0], evaluate_state(goal, evaluate)[0]))
    assert live == tuple(bddl) == expected


@pytest.mark.parametrize("variant_id", ["A", "B"])
def test_query_can_complete_after_another_cabinet_was_opened(world, variant_id):
    objects, facts, _ = world(variant_id, "target")
    facts.add(("open", objects["cabinet.n.01_2"].name))
    assert not TASK.check_goal("query", objects, variant_id)
    facts.remove(("open", objects["cabinet.n.01_2"].name))
    chocolate = objects["chocolate_bar.n.01_1"].name
    facts.remove(("inside", chocolate, objects["cabinet.n.01_1"].name))
    facts.add(("ontop", chocolate, objects["countertop.n.01_1"].name))
    assert TASK.check_goal("query", objects, variant_id)


def test_balanced_variants_change_only_location_and_key_instruction():
    assert get_task(TASK.task_name) is TASK
    assert Counter(TASK.sample_variants(150, 0).values()) == {"A": 75, "B": 75}
    variants = [bindings_for_variant(variant_id) for variant_id in TASK.variants]
    assert variants[0]["cabinet.n.01_1"] != variants[1]["cabinet.n.01_1"]
    assert set(variants[0].values()) == set(variants[1].values())
    assert variants[0]["chocolate_bar.n.01_1"] == variants[1]["chocolate_bar.n.01_1"]
    assert variants[0]["countertop.n.01_1"] == variants[1]["countertop.n.01_1"]
    key_instructions = {TASK.instruction_for(TASK.key_activities[0], variant_id) for variant_id in TASK.variants}
    query_instructions = {TASK.instruction_for(TASK.query_activity, variant_id) for variant_id in TASK.variants}
    assert len(key_instructions) == 2
    assert query_instructions == {TASK.query_instruction}


def test_query_uses_fixed_instruction_even_when_key_instructions_are_missing():
    task = replace(
        TASK,
        query_instruction="Retrieve the chocolate.",
        key_instructions={},
    )
    assert task.instruction_for(task.query_activity, "A") == "Retrieve the chocolate."
    assert task.instruction_for(task.query_activity) == "Retrieve the chocolate."


@pytest.mark.parametrize("variant_id,instructions", [
    (None, TASK.key_instructions),
    ("A", {}),
    ("A", {"A": {}}),
])
def test_key_instruction_requires_variant_and_explicit_instruction(variant_id, instructions):
    task = replace(TASK, key_instructions=instructions)
    with pytest.raises(KeyError, match="No Key instruction declared"):
        task.instruction_for(task.key_activities[0], variant_id)


def test_sampling_constraints_use_valid_bddl_categories():
    taxonomy = ObjectTaxonomy()
    for activity in (*TASK.key_activities, TASK.query_activity):
        conditions = Conditions(activity, 0, "behavior-1k")
        assert all(taxonomy.is_valid_synset(synset) for synset in conditions.parsed_objects)
        for synset, categories in TASK.whitelist_for(activity).items():
            assert synset in conditions.parsed_objects
            assert set(categories) <= set(taxonomy.get_subtree_categories(synset))
