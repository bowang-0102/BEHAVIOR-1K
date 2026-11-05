from copy import deepcopy

import pytest

from memory_bench.tasks import TASKS
from memory_bench.utils.task_segments import check_frame


@pytest.mark.parametrize("task", TASKS.values(), ids=TASKS.keys())
def test_all_task_segments_have_initial_conditions(task):
    required_entries = {f"key_{index}" for index in range(1, len(task.key_activities) + 1)} | {"query"}
    assert required_entries <= set(task.init)


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
    assert check_frame(segment, unrelated, baseline, set(usage["protected"])) == {"unrelated"}
