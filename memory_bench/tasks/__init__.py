from memory_bench.tasks.mb000_retrieve_bottle_of_milk import TASK as TASK_000
from memory_bench.tasks.mb001_return_book_to_original_bookcase import TASK as TASK_001
from memory_bench.tasks.mb002_place_reading_lamp_by_previous_state import TASK as TASK_002
from memory_bench.tasks.mb003_place_radio_by_previous_state import TASK as TASK_003
from memory_bench.tasks.mb004_put_away_mug_and_tissue_box import TASK as TASK_004
from memory_bench.tasks.mb005_return_bowl_and_cutting_board import TASK as TASK_005
from memory_bench.tasks.mb006_retrieve_relocated_meat_jar import TASK as TASK_006
from memory_bench.tasks.mb007_place_gaming_controller_on_original_nightstand import TASK as TASK_007

TASKS = {task.task_name: task for task in (TASK_000, TASK_001, TASK_002, TASK_003, TASK_004, TASK_005, TASK_006, TASK_007)}


def get_task(session_id: str):
    """Return the task definition registered for a session ID."""
    try:
        return TASKS[session_id]
    except KeyError as error:
        raise KeyError(f"No Memory Bench task is registered for session {session_id!r}") from error


__all__ = ["TASKS", "get_task"]
