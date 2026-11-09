from copy import deepcopy

import pytest

from memory_bench.tasks import TASKS
from memory_bench.utils.task_segments import check_frame


@pytest.mark.parametrize("task", TASKS.values(), ids=TASKS.keys())
def test_all_task_variants_have_segment_conditions(task):
    required_entries = {f"key_{index}" for index in range(1, len(task.key_activities) + 1)} | {"query"}
    for conditions in (task.init, task.goal):
        assert set(conditions) == set(task.variants)
        for variant_id in task.variants:
            assert required_entries <= set(conditions[variant_id])


@pytest.mark.parametrize("task", TASKS.values(), ids=TASKS.keys())
def test_all_variants_provide_instructions_without_sampled_scene(task):
    for variant_id in task.variants:
        for activity in task.key_activities:
            assert task.instruction_for(activity, variant_id)
        assert task.instruction_for(task.query_activity, variant_id) == task.query_instruction


@pytest.mark.parametrize("task", TASKS.values(), ids=TASKS.keys())
def test_query_only_allows_declared_cabinet_writes(task):
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
        if cabinet in usage["write"]:
            assert check_frame(segment, opened, baseline, set(usage["protected"])) == {cabinet}
        else:
            with pytest.raises(ValueError, match="altered protected memory"):
                check_frame(segment, opened, baseline, set(usage["protected"]))

    unrelated = deepcopy(baseline)
    unrelated["object_registry"]["unrelated"]["joint_pos"] = [0.5]
    assert check_frame(segment, unrelated, baseline, set(usage["protected"])) == {"unrelated"}
