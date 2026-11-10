from memory_bench.tasks.mb000_retrieve_milk import TASK as TASK_000
from memory_bench.tasks.mb001_putting_book_back_on_bookshelf import TASK as TASK_001
from memory_bench.tasks.mb002_restore_reading_lamp import TASK as TASK_002
from memory_bench.tasks.mb003_putting_radio_on_tv_stand import TASK as TASK_003
from memory_bench.tasks.mb004_returning_tea_supplies import TASK as TASK_004
from memory_bench.tasks.mb005_returning_meal_prep_supplies import TASK as TASK_005
from memory_bench.tasks.mb006_returning_hamburger_to_original_counter import TASK as TASK_006
from memory_bench.tasks.mb007_returning_controller_to_original_nightstand import TASK as TASK_007

TASKS = {task.task_name: task for task in (TASK_000, TASK_001, TASK_002, TASK_003, TASK_004, TASK_005, TASK_006, TASK_007)}


def get_task(session_id: str):
    """Return the task definition registered for a session ID."""
    try:
        return TASKS[session_id]
    except KeyError as error:
        raise KeyError(f"No Memory Bench task is registered for session {session_id!r}") from error


__all__ = ["TASKS", "get_task"]
