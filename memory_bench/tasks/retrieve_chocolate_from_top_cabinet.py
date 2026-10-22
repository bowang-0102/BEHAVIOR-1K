"""Level 1 chocolate retrieval with nearby living-room and corridor interventions."""

from memory_bench.tasks.base_task import MemoryTask


TASK = MemoryTask(
    # Task and scene.
    task_name="mb_retrieve_chocolate_from_top_cabinet",
    scene_model="house_double_floor_lower",
    room_types=("kitchen",),

    # Sampling constraints.
    whitelist={
        "chocolate_bar.n.01": {"chocolate_bar": {"dxnisi": None}},
        "cabinet.n.01": {"top_cabinet": {"tynnnw": None}},
        "countertop.n.01": {"bar": {"egwapq": None}},
    },

    # Activities and instructions.
    key_activities=("mb_chocolate_into_top_cabinet",),
    intervent_activities=("turning_on_radio", "putting_shoes_on_rack"),
    query_activity="mb_retrieve_chocolate_from_top_cabinet",
    query_instruction=(
        "Retrieve the chocolate bar you stored earlier and place it on the kitchen counter below the top cabinets."
    ),
    # The two cabinets are aligned along the kitchen/living-room direction.
    # Only Key instructions identify the selected cabinet; Query never does.
    key_instructions={
        "A": {
            "mb_chocolate_into_top_cabinet": (
                "Put the chocolate bar from the kitchen counter below the top cabinets into the top cabinet "
                "farther from the living room and close both cabinets."
            ),
        },
        "B": {
            "mb_chocolate_into_top_cabinet": (
                "Put the chocolate bar from the kitchen counter below the top cabinets into the top cabinet "
                "nearer the living room and close both cabinets."
            ),
        },
    },

    # Variant bindings and sampling.
    variants={
        "A": {"cabinet.n.01_1": "top_cabinet_tynnnw_0"},
        "B": {"cabinet.n.01_1": "top_cabinet_tynnnw_1"},
    },

    # Object access.
    object_usage={
        "mb_chocolate_into_top_cabinet": {
            "used": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "countertop.n.01_1"),
            "read": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "countertop.n.01_1"),
            "write": ("chocolate_bar.n.01_1", "cabinet.n.01_1"),
            "protected": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "countertop.n.01_1"),
        },
        "mb_retrieve_chocolate_from_top_cabinet": {
            "used": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "countertop.n.01_1"),
            "read": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "countertop.n.01_1"),
            "write": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2"),
            "protected": ("chocolate_bar.n.01_1", "cabinet.n.01_1", "cabinet.n.01_2", "countertop.n.01_1"),
        },
    },

    # BDDL initial and goal conditions.
    init={
        "key_1": [
            ["ontop", "chocolate_bar.n.01_1", "countertop.n.01_1"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ],
        "query": [
            ["inside", "chocolate_bar.n.01_1", "cabinet.n.01_1"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ],
    },
    goal={
        "key_1": [
            ["inside", "chocolate_bar.n.01_1", "cabinet.n.01_1"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ],
        "query": [
            ["ontop", "chocolate_bar.n.01_1", "countertop.n.01_1"],
            ["forall", ["?cabinet", "-", "cabinet.n.01"], ["not", ["open", "?cabinet"]]],
        ],
    },
)
