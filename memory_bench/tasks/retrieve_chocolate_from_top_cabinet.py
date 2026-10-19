"""Level 1 chocolate retrieval with nearby living-room and corridor interventions."""

from memory_bench.tasks.base_task import MemoryTask


TASK = MemoryTask(
    task_name="mb_retrieve_chocolate_from_top_cabinet",
    scene_model="house_double_floor_lower",
    key_activities=("mb_chocolate_into_top_cabinet",),
    intervent_activities=("turning_on_radio", "putting_shoes_on_rack"),
    query_activity="mb_retrieve_chocolate_from_top_cabinet",
    query_instruction="Retrieve the chocolate bar you stored earlier and place it on the breakfast table.",
    room_types=("kitchen",),
    whitelist={
        "chocolate_bar.n.01": {"chocolate_bar": {"dxnisi": None}},
        "cabinet.n.01": {"top_cabinet": {"tynnnw": None}},
        "breakfast_table.n.01": {"breakfast_table": {"xftrki": None}},
    },
    variants={
        "A": {"cabinet.n.01_1": "top_cabinet_tynnnw_0"},
        "B": {"cabinet.n.01_1": "top_cabinet_tynnnw_1"},
    },
    segment_instructions={
        "mb_chocolate_into_top_cabinet": (
            "Put the chocolate bar from the breakfast table into a top cabinet and close both cabinets."
        ),
        "mb_retrieve_chocolate_from_top_cabinet": (
            "Retrieve the chocolate bar you stored earlier and place it on the breakfast table."
        ),
    },
    # The two cabinets are aligned along the kitchen/living-room direction.
    # Only Key instructions identify the selected cabinet; Query never does.
    case_instructions={
        "A": {
            "mb_chocolate_into_top_cabinet": (
                "Put the chocolate bar from the breakfast table into the top cabinet farther from the living room "
                "and close both cabinets."
            ),
        },
        "B": {
            "mb_chocolate_into_top_cabinet": (
                "Put the chocolate bar from the breakfast table into the top cabinet nearer the living room "
                "and close both cabinets."
            ),
        },
    },
    key_information={
        "key_1": {
            "provides": "The chocolate bar's selected top-cabinet location (case A or B).",
            "bindings": {"chocolate_bar.n.01_1": "chocolate_bar", "cabinet.n.01_1": "selected_top_cabinet"},
            "updates": {},
        }
    },
    key_dependencies={"key_1": ()},
    object_usage={
        "mb_chocolate_into_top_cabinet": {
            "used": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "breakfast_table.n.01_1"),
            "read": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "breakfast_table.n.01_1"),
            "write": ("chocolate_bar.n.01_1", "cabinet.n.01_1"),
            "protected": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "breakfast_table.n.01_1"),
        },
        "mb_retrieve_chocolate_from_top_cabinet": {
            "used": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "breakfast_table.n.01_1"),
            "read": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "breakfast_table.n.01_1"),
            "write": ("chocolate_bar.n.01_1", "cabinet.n.01_1"),
            "protected": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "breakfast_table.n.01_1"),
        },
    },
    entry_checks={
        "key_1": (
            ("ontop", ("chocolate_bar.n.01_1", "breakfast_table.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2")),
        ),
        "query": (
            ("inside", ("chocolate_bar.n.01_1", "cabinet.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2")),
        ),
    },
    segment_checks={
        "key_1": (
            ("inside", ("chocolate_bar.n.01_1", "cabinet.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2")),
        ),
        "query": (
            ("ontop", ("chocolate_bar.n.01_1", "breakfast_table.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2")),
        ),
    },
    query_cabinets=("cabinet.n.01_1", "cabinet.n.01_2"),
    query_target_cabinets={case_id: ("cabinet.n.01_1",) for case_id in ("A", "B")},
)
