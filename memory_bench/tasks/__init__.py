"""Registered task definitions, keyed by their Query BDDL activity name."""

from memory_bench.tasks.integrate_mug_plate_locations import TASK as _INTEGRATION_TASK
from memory_bench.tasks.retrieve_mug_from_top_cabinet import TASK as _MUG_RETRIEVAL_TASK
from memory_bench.tasks.update_mug_location import TASK as _MUG_UPDATE_TASK

TASKS = {task.task_name: task for task in (_MUG_RETRIEVAL_TASK, _MUG_UPDATE_TASK, _INTEGRATION_TASK)}


def get_task(session_id: str):
    """Return the task definition registered for a session's Query activity."""
    try:
        return TASKS[session_id]
    except KeyError as error:
        raise KeyError(f"No Memory Bench task is registered for session {session_id!r}") from error


__all__ = ["TASKS", "get_task"]
