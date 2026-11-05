from memory_bench.tasks.retrieve_baking_soda_from_top_cabinet import TASK as _LEGACY_TASK
from memory_bench.tasks.retrieve_stored_flashlight import TASK as _TASK_000
from memory_bench.tasks.return_borrowed_cup import TASK as _TASK_001
from memory_bench.tasks.resume_cabinet_storage import TASK as _TASK_002
from memory_bench.tasks.resume_toolbox_packing import TASK as _TASK_003
from memory_bench.tasks.restore_coffee_supplies import TASK as _TASK_004
from memory_bench.tasks.restore_tea_supplies import TASK as _TASK_005
from memory_bench.tasks.retrieve_relocated_cup import TASK as _TASK_006
from memory_bench.tasks.retrieve_packed_flashlight import TASK as _TASK_007

TASKS = {
    task.task_name: task
    for task in (_LEGACY_TASK, _TASK_000, _TASK_001, _TASK_002, _TASK_003, _TASK_004, _TASK_005, _TASK_006, _TASK_007)
}


def get_task(session_id: str):
    """Return the task definition registered for a session's Query activity."""
    try:
        return TASKS[session_id]
    except KeyError as error:
        raise KeyError(f"No Memory Bench task is registered for session {session_id!r}") from error


__all__ = ["TASKS", "get_task"]
