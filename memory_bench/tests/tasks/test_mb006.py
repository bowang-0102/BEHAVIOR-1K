from collections import Counter
from itertools import permutations
from types import SimpleNamespace

import pytest
from bddl.condition_evaluation import compile_state
from bddl.parsing import parse_problem
from omnigibson.object_states import Cooked, Inside, OnTop, Open
from omnigibson.utils.bddl_utils import BDDLSampler

from memory_bench.tasks.mb006_retrieve_relocated_meat_jar import TASK


def test_variants_cover_all_directed_cabinet_moves_evenly():
    moves = {
        (bindings["cabinet.n.01_1"], bindings["cabinet.n.01_2"])
        for bindings in TASK.variants.values()
    }
    cabinets = {f"top_cabinet_lkxmne_{index}" for index in range(3)}
    assert moves == set(permutations(cabinets, 2))
    assert Counter(TASK.sample_variants(150, seed=0).values()) == {
        variant: 25 for variant in TASK.variants
    }


@pytest.mark.parametrize("segment,activity", [
    ("key_1", TASK.key_activities[0]),
    ("key_2", TASK.key_activities[1]),
    ("query", TASK.query_activity),
])
def test_sampler_bddl_matches_runtime_conditions(segment, activity):
    _, objects, initial, goal = parse_problem(activity, 0, "behavior-1k")
    scope = {instance for instances in objects.values() for instance in instances}

    def normalize(expression):
        if isinstance(expression, list):
            return [normalize(value) for value in expression]
        return expression.removeprefix("?")

    # Room assignments and the robot's floor relation are sampler-only setup.
    initial = [
        condition for condition in initial
        if condition[0] != "inroom" and condition[1] != "agent.n.01_1"
    ]
    for variant in TASK.variants:
        # Negative containment checks remain runtime-only: the sampler rejects them.
        runtime_initial = [
            condition for condition in TASK.init[variant][segment]
            if not (condition[0] == "not" and condition[1][0] == "inside")
        ]
        assert initial == runtime_initial
        assert normalize(goal) == TASK.goal[variant][segment]
    assert set(TASK.object_usage[activity]["used"]) <= scope


@pytest.mark.parametrize("activity", [*TASK.key_activities, TASK.query_activity])
def test_sampler_accepts_initial_conditions(activity):
    _, objects, initial, _ = parse_problem(activity, 0, "behavior-1k")
    scope = {instance for instances in objects.values() for instance in instances}
    sampler = BDDLSampler.__new__(BDDLSampler)
    sampler._scene_model = TASK.scene_model
    sampler._object_scope = dict.fromkeys(scope)
    sampler._substance_instances = set()
    sampler._inroom_object_instances = {condition[1] for condition in initial if condition[0] == "inroom"}
    sampler._compiled_task = SimpleNamespace(initial_conditions=compile_state(
        [condition for condition in initial if condition[0] != "inroom"], scope=scope, object_map=objects,
    ))
    assert sampler._build_sampling_order() is None


@pytest.fixture
def physical_objects():
    names = {*TASK.variants["AB"].values(), "jar", "sausage", "board"}
    objects = {
        name: SimpleNamespace(
            name=name, initialized=True, inside=None, on_top=None, opened=False, cooked=False, states={}
        )
        for name in names
    }
    for obj in objects.values():
        obj.states = {
            Inside: SimpleNamespace(get_value=lambda other, obj=obj: obj.inside is other),
            OnTop: SimpleNamespace(get_value=lambda other, obj=obj: obj.on_top is other),
            Open: SimpleNamespace(get_value=lambda obj=obj: obj.opened),
            Cooked: SimpleNamespace(get_value=lambda obj=obj: obj.cooked),
        }
    return objects


@pytest.mark.parametrize("variant", TASK.variants)
def test_live_flow_requires_relocation_and_preserves_sealed_contents(physical_objects, variant):
    base = {
        **TASK.variants["AB"],
        "hinged_jar.n.01_1": "jar",
        "bratwurst.n.01_1": "sausage",
        "chopping_board.n.01_1": "board",
    }
    bindings = TASK.bind_variant(base, variant)
    objects = {instance: physical_objects[name] for instance, name in bindings.items()}
    jar = objects["hinged_jar.n.01_1"]
    sausage = objects["bratwurst.n.01_1"]
    board = objects["chopping_board.n.01_1"]
    bar = objects["countertop.n.01_1"]
    old = objects["cabinet.n.01_1"]
    new = objects["cabinet.n.01_2"]
    unused = objects["cabinet.n.01_3"]
    jar.inside = old
    sausage.on_top = board
    sausage.cooked = True
    board.on_top = bar

    assert TASK.check_init("key_1", objects, variant)
    assert not TASK.check_goal("key_1", objects, variant)

    # Packing and storing establishes the old location and the next segment's entry.
    sausage.on_top = None
    sausage.inside = jar
    assert TASK.check_goal("key_1", objects, variant)
    assert TASK.check_init("key_2", objects, variant)
    assert not TASK.check_goal("key_2", objects, variant)
    assert not TASK.check_init("query", objects, variant)

    # Neither the old location nor the third cabinet may satisfy the updated location.
    jar.inside = unused
    assert not TASK.check_goal("key_2", objects, variant)
    assert not TASK.check_init("query", objects, variant)
    jar.inside = new
    assert TASK.check_goal("key_2", objects, variant)
    assert TASK.check_init("query", objects, variant)
    assert not TASK.check_goal("query", objects, variant)

    # Every candidate door must be closed at the boundary, including the unused one.
    unused.opened = True
    assert not TASK.check_goal("key_2", objects, variant)
    assert not TASK.check_init("query", objects, variant)
    unused.opened = False

    jar.inside = None
    jar.on_top = bar
    assert TASK.check_goal("query", objects, variant)
    sausage.inside = None
    assert not TASK.check_goal("query", objects, variant)
    sausage.inside = jar
    jar.opened = True
    assert not TASK.check_goal("query", objects, variant)
    jar.opened = False
    assert TASK.check_goal("query", objects, variant)
