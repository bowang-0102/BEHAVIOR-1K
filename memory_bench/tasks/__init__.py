from memory_bench.tasks.retrieve_baking_soda_from_top_cabinet import TASK as _TASK

TASKS = {_TASK.task_name: _TASK}


def get_task(session_id: str):
    """Return the task definition registered for a session's Query activity."""
    try:
        return TASKS[session_id]
    except KeyError as error:
        raise KeyError(f"No Memory Bench task is registered for session {session_id!r}") from error


__all__ = ["TASKS", "get_task"]
