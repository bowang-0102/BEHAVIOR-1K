from memory_bench.tasks.base_task import MemoryTask

TASK = MemoryTask(
    # Task and scene.
    task_name="mb001_return_book_to_original_bookcase",
    scene_model="house_double_floor_upper",
    room_types=("living_room",),

    # Sampling constraints.
    whitelist={
        "hardback.n.01": {"hardback": {"acbrnv": None}},
        "bookcase.n.01": {"bookcase": {"otwukr": None}},
        "desk.n.01": {"desk": {"iotfzl": None}},
    },

    # Activities and instructions.
    key_activities=("mb_place_book_on_desk",),
    intervent_activities=("organizing_art_supplies", "getting_organized_for_work"),
    query_activity="mb_return_book_to_original_bookcase",
    query_instruction="Return the book from the desk to the top of the exact same bookcase you took it from earlier.",
    key_instructions={
        "A": {
            "mb_place_book_on_desk": (
                "Place the book from the top of the bookcase on the desk."
            ),
        },
        "B": {
            "mb_place_book_on_desk": (
                "Place the book from the top of the bookcase on the desk."
            ),
        },
    },

    # Variant bindings and sampling.
    # Include all four existing bookcases so native sampling retains both candidates.
    # Use the bookcases at opposite ends of the row as the two sources.
    # The other two bookcases remain protected background supports.
    variants={
        "A": {
            "bookcase.n.01_1": "bookcase_otwukr_0",
            "bookcase.n.01_2": "bookcase_otwukr_2",
            "bookcase.n.01_3": "bookcase_otwukr_1",
            "bookcase.n.01_4": "bookcase_otwukr_3",
            "desk.n.01_1": "desk_iotfzl_0",
        },
        "B": {
            "bookcase.n.01_1": "bookcase_otwukr_2",
            "bookcase.n.01_2": "bookcase_otwukr_0",
            "bookcase.n.01_3": "bookcase_otwukr_1",
            "bookcase.n.01_4": "bookcase_otwukr_3",
            "desk.n.01_1": "desk_iotfzl_0",
        },
    },

    # Object access.
    object_usage={
        "mb_place_book_on_desk": {
            "used": ("hardback.n.01_1", "bookcase.n.01_1", "bookcase.n.01_2", "bookcase.n.01_3", "bookcase.n.01_4", "desk.n.01_1"),
            "read": ("hardback.n.01_1", "bookcase.n.01_1", "bookcase.n.01_2", "bookcase.n.01_3", "bookcase.n.01_4", "desk.n.01_1"),
            "write": ("hardback.n.01_1",),
            "protected": ("hardback.n.01_1", "bookcase.n.01_1", "bookcase.n.01_2", "bookcase.n.01_3", "bookcase.n.01_4", "desk.n.01_1"),
        },
        "mb_return_book_to_original_bookcase": {
            "used": ("hardback.n.01_1", "bookcase.n.01_1", "bookcase.n.01_2", "bookcase.n.01_3", "bookcase.n.01_4", "desk.n.01_1"),
            "read": ("hardback.n.01_1", "bookcase.n.01_1", "bookcase.n.01_2", "bookcase.n.01_3", "bookcase.n.01_4", "desk.n.01_1"),
            "write": ("hardback.n.01_1",),
            "protected": ("hardback.n.01_1", "bookcase.n.01_1", "bookcase.n.01_2", "bookcase.n.01_3", "bookcase.n.01_4", "desk.n.01_1"),
        },
    },

    # BDDL initial and goal conditions.
    init={
        "A": {
            "key_1": [["ontop", "hardback.n.01_1", "bookcase.n.01_1"]],
            "query": [["ontop", "hardback.n.01_1", "desk.n.01_1"]],
        },
        "B": {
            "key_1": [["ontop", "hardback.n.01_1", "bookcase.n.01_1"]],
            "query": [["ontop", "hardback.n.01_1", "desk.n.01_1"]],
        },
    },
    goal={
        "A": {
            "key_1": [["ontop", "hardback.n.01_1", "desk.n.01_1"]],
            "query": [["ontop", "hardback.n.01_1", "bookcase.n.01_1"]],
        },
        "B": {
            "key_1": [["ontop", "hardback.n.01_1", "desk.n.01_1"]],
            "query": [["ontop", "hardback.n.01_1", "bookcase.n.01_1"]],
        },
    },
)
