"""Offline invariants for the Level 2 memory-update task variants."""

from memory_bench.tasks import TASKS
from memory_bench.tasks.update_mug_location import TASK


def _binding(case_id, activity):
    base = {
        "mug.n.04_1": "mug_231",
        "mug.n.04_2": "mug_232",
        "cabinet.n.01_1": "top_cabinet_lkxmne_0",
        "cabinet.n.01_2": "top_cabinet_lkxmne_1",
        "cabinet.n.01_3": "top_cabinet_lkxmne_2",
        "countertop.n.01_1": "bar_udatjt_0",
    }
    return TASK.bind_variant(base, case_id, activity, {"tall_mug": "mug_231", "short_mug": "mug_232"})


def _pairs(case_id, activity, segment):
    binding = _binding(case_id, activity)
    checks = TASK.case_segment_checks.get(case_id, {}).get(segment, TASK.segment_checks[segment])
    return {(binding[instances[0]], binding[instances[1]]) for predicate, instances in checks if predicate == "inside"}


def test_key1_arrangement_does_not_reveal_target_identity():
    tall = "tall_narrow_right_shift"
    short = "short_wide_right_shift"
    assert _pairs(tall, TASK.key_activities[0], "key_1") == _pairs(short, TASK.key_activities[0], "key_1")
    assert _binding(tall, TASK.key_activities[0])["mug.n.04_1"] != _binding(short, TASK.key_activities[0])["mug.n.04_1"]
    assert TASK.instruction_for(TASK.key_activities[0], tall) != TASK.instruction_for(TASK.key_activities[0], short)
    assert "key_1" in TASK.entry_checks


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
        assert TASK.entry_checks["key_2"] == (
            ("inside", ("mug.n.04_1", "cabinet.n.01_1")),
            ("inside", ("mug.n.04_2", "cabinet.n.01_2")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3")),
        )


def test_query_requires_identity_and_updated_location():
    for task in TASKS.values():
        required_entries = {f"key_{index}" for index in range(1, len(task.key_activities) + 1)} | {"query"}
        assert required_entries <= set(task.entry_checks)
    assert TASK.entry_checks["query"] == (
        ("inside", ("mug.n.04_1", "cabinet.n.01_1")),
        ("inside", ("mug.n.04_2", "cabinet.n.01_2")),
        ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3")),
    )
    target_locations = set()
    for case_id in TASK.variants:
        query = _binding(case_id, TASK.query_activity)
        key2_pairs = _pairs(case_id, TASK.key_activities[1], "key_2")
        target_mug = query["mug.n.04_1"]
        expected_target_cabinet = dict(key2_pairs)[target_mug]
        assert query["cabinet.n.01_1"] == expected_target_cabinet
        target_locations.add((target_mug, expected_target_cabinet))
    assert len(target_locations) == 4
