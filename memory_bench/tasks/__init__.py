from memory_bench.tasks.mb001_putting_book_back_on_bookshelf import TASK as TASK_001
from memory_bench.tasks.mb_retrieve_baking_soda_from_top_cabinet import TASK as _DEMO

TASKS = {task.task_name: task for task in (_DEMO, TASK_001)}


def get_task(session_id: str):
    """Return the task definition registered for a session ID."""
    try:
        return TASKS[session_id]
    except KeyError as error:
        raise KeyError(f"No Memory Bench task is registered for session {session_id!r}") from error


__all__ = ["TASKS", "get_task"]
