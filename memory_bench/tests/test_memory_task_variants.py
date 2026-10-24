from copy import deepcopy
from types import SimpleNamespace

import pytest
from omnigibson.object_states import Inside, OnTop, Open

from memory_bench.tasks import TASKS
from memory_bench.tasks.update_mug_location import TASK
from memory_bench.utils.task_segments import check_frame


def _binding(variant_id, activity):
    base = {
        "mug.n.04_1": "mug_231",
        "mug.n.04_2": "mug_232",
        "cabinet.n.01_1": "top_cabinet_lkxmne_0",
        "cabinet.n.01_2": "top_cabinet_lkxmne_1",
        "cabinet.n.01_3": "top_cabinet_lkxmne_2",
        "countertop.n.01_1": "bar_udatjt_0",
    }
    return TASK.bind_variant(base, variant_id, activity, {"tall_mug": "mug_231", "short_mug": "mug_232"})


def _pairs(variant_id, activity, segment):
    binding = _binding(variant_id, activity)
    conditions = TASK.variant_goal.get(variant_id, {}).get(segment, TASK.goal[segment])
    return {
        (binding[instances[0]], binding[instances[1]]) for predicate, *instances in conditions if predicate == "inside"
    }


def test_key1_arrangement_does_not_reveal_target_identity():
    tall = "tall_narrow_right_shift"
    short = "short_wide_right_shift"
    assert _pairs(tall, TASK.key_activities[0], "key_1") == _pairs(short, TASK.key_activities[0], "key_1")
    assert _binding(tall, TASK.key_activities[0])["mug.n.04_1"] != _binding(short, TASK.key_activities[0])["mug.n.04_1"]
    assert TASK.instruction_for(TASK.key_activities[0], tall) != TASK.instruction_for(TASK.key_activities[0], short)
    assert "key_1" in TASK.init


def test_key2_is_target_independent_and_updates_both_physical_mugs():
    for permutation in ("right_shift", "swap"):
        tall = f"tall_narrow_{permutation}"
        short = f"short_wide_{permutation}"
        assert _binding(tall, TASK.key_activities[1]) == _binding(short, TASK.key_activities[1])
        assert TASK.instruction_for(TASK.key_activities[1], tall) == TASK.instruction_for(TASK.key_activities[1], short)
        moved = _pairs(tall, TASK.key_activities[1], "key_2")
        assert moved == _pairs(short, TASK.key_activities[1], "key_2")
        assert len(moved) == 2
        assert {source for source, _ in moved} == {"mug_231", "mug_232"}
        assert TASK.init["key_2"] == [
            ["inside", "mug.n.04_1", "cabinet.n.01_1"],
            ["inside", "mug.n.04_2", "cabinet.n.01_2"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ]


def test_query_requires_identity_and_updated_location():
    for task in TASKS.values():
        required_entries = {f"key_{index}" for index in range(1, len(task.key_activities) + 1)} | {"query"}
        assert required_entries <= set(task.init)
    assert TASK.init["query"] == [
        ["inside", "mug.n.04_1", "cabinet.n.01_1"],
        ["inside", "mug.n.04_2", "cabinet.n.01_2"],
        ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
    ]
    target_locations = set()
    for variant_id in TASK.variants:
        query = _binding(variant_id, TASK.query_activity)
        key2_pairs = _pairs(variant_id, TASK.key_activities[1], "key_2")
        target_mug = query["mug.n.04_1"]
        expected_target_cabinet = dict(key2_pairs)[target_mug]
        assert query["cabinet.n.01_1"] == expected_target_cabinet
        target_locations.add((target_mug, expected_target_cabinet))
    assert len(target_locations) == 4


@pytest.mark.parametrize("variant_id", TASK.variants)
def test_live_conditions_follow_each_mug_through_all_stages(variant_id):
    bindings = {
        segment: _binding(variant_id, activity)
        for segment, activity in zip(("key_1", "key_2", "query"), (*TASK.key_activities, TASK.query_activity))
    }
    objects = {name: SimpleNamespace(name=name, initialized=True) for name in bindings["key_1"].values()}
    facts = set()
    for obj in objects.values():
        obj.states = {
            Inside: SimpleNamespace(get_value=lambda other, obj=obj: ("inside", obj.name, other.name) in facts),
            OnTop: SimpleNamespace(get_value=lambda other, obj=obj: ("ontop", obj.name, other.name) in facts),
            Open: SimpleNamespace(get_value=lambda obj=obj: ("open", obj.name) in facts),
        }
    live = {segment: {instance: objects[name] for instance, name in binding.items()}
            for segment, binding in bindings.items()}
    TASK.prepare_conditions(bindings, variant_id)
    counter = "bar_udatjt_0"
    cabinets = [f"top_cabinet_lkxmne_{index}" for index in range(3)]
    facts.update(("ontop", mug, counter) for mug in ("mug_231", "mug_232"))
    assert TASK.check_init("key_1", live["key_1"], variant_id)
    assert not TASK.check_goal("key_1", live["key_1"], variant_id)

    facts.clear()
    facts.update({("inside", "mug_231", cabinets[0]), ("inside", "mug_232", cabinets[1])})
    assert TASK.check_goal("key_1", live["key_1"], variant_id)
    assert TASK.check_init("key_2", live["key_2"], variant_id)
    assert not TASK.check_goal("key_2", live["key_2"], variant_id)

    destinations = (1, 2) if variant_id.endswith("right_shift") else (2, 0)
    facts.clear()
    facts.update({("inside", "mug_231", cabinets[destinations[0]]),
                  ("inside", "mug_232", cabinets[destinations[1]])})
    assert TASK.check_goal("key_2", live["key_2"], variant_id)
    assert TASK.check_init("query", live["query"], variant_id)
    assert not TASK.check_goal("query", live["query"], variant_id)

    target = bindings["query"]["mug.n.04_1"]
    other = bindings["query"]["mug.n.04_2"]
    final_facts = facts.copy()
    facts.difference_update(fact for fact in final_facts if fact[1] == other)
    facts.add(("ontop", other, counter))
    assert not TASK.check_goal("query", live["query"], variant_id)

    facts.clear()
    facts.update(fact for fact in final_facts if fact[1] != target)
    facts.add(("ontop", target, counter))
    assert TASK.check_goal("query", live["query"], variant_id)
    for cabinet in cabinets:
        facts.add(("open", cabinet))
        assert not TASK.check_goal("query", live["query"], variant_id)
        facts.remove(("open", cabinet))
    assert TASK.check_goal("query", live["query"], variant_id)


@pytest.mark.parametrize("task", TASKS.values(), ids=TASKS.keys())
def test_query_write_scope_allows_all_task_cabinets(task):
    usage = task.object_usage[task.query_activity]
    baseline = {
        "object_registry": {name: {"joint_pos": [0.0]} for name in [*usage["used"], "robot", "unrelated"]},
        "system_registry": {},
    }
    segment = {
        "identity": {"id": "query"},
        "task": {"role": "query_task"},
        "interface": {"robot_name": "robot"},
        "objects": {"write": usage["write"]},
    }
    for cabinet in (name for name in usage["used"] if name.startswith("cabinet.n.")):
        opened = deepcopy(baseline)
        opened["object_registry"][cabinet]["joint_pos"] = [0.5]
        check_frame(segment, opened, baseline, set(usage["protected"]))

    unrelated = deepcopy(baseline)
    unrelated["object_registry"]["unrelated"]["joint_pos"] = [0.5]
    with pytest.raises(ValueError, match="Undeclared state changes"):
        check_frame(segment, unrelated, baseline, set(usage["protected"]))
