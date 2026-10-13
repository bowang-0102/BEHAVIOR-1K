"""Level 1 mug retrieval using one remembered cabinet location."""

from memory_bench.tasks.base_task import MemoryTask


TASK = MemoryTask(
    task_name="mb_retrieve_mug_from_top_cabinet",
    scene_model="house_single_floor",
    key_activities=("mb_mug_into_top_cabinet",),
    intervent_activities=("tidying_bedroom", "outfit_a_basic_toolbox"),
    query_activity="mb_retrieve_mug_from_top_cabinet",
    query_instruction="Retrieve the mug and place it on the kitchen island.",
    room_types=("kitchen",),
    whitelist={
        "mug.n.04": {"mug": {"ntgftr": None}},
        "cabinet.n.01": {"top_cabinet": {"lkxmne": None}},
        "countertop.n.01": {"bar": {"udatjt": None}},
    },
    variants={
        "A": {"cabinet.n.01_1": "top_cabinet_lkxmne_0"},
        "B": {"cabinet.n.01_1": "top_cabinet_lkxmne_1"},
        "C": {"cabinet.n.01_1": "top_cabinet_lkxmne_2"},
    },
    segment_instructions={
        "mb_mug_into_top_cabinet": "Put the mug from the kitchen island into a top cabinet and close the door.",
        "mb_retrieve_mug_from_top_cabinet": "Retrieve the mug and place it on the kitchen island.",
    },
    key_information={
        "key_1": {
            "provides": "The mug's selected top-cabinet location (case A, B, or C).",
            "bindings": {"mug.n.04_1": "mug", "cabinet.n.01_1": "selected_top_cabinet"},
            "updates": {},
        }
    },
    key_dependencies={"key_1": ()},
    object_usage={
        "mb_mug_into_top_cabinet": {
            "used": ("mug.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3", "countertop.n.01_1"),
            "read": ("mug.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3", "countertop.n.01_1"),
            "write": ("mug.n.04_1", "cabinet.n.01_1"),
            "protected": ("mug.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3"),
        },
        "mb_retrieve_mug_from_top_cabinet": {
            "used": ("mug.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3", "countertop.n.01_1"),
            "read": ("mug.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3", "countertop.n.01_1"),
            "write": ("mug.n.04_1", "cabinet.n.01_1"),
            "protected": ("mug.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3"),
        },
    },
    segment_checks={
        "key_1": (
            ("inside", ("mug.n.04_1", "cabinet.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3")),
        ),
        "query": (
            ("ontop", ("mug.n.04_1", "countertop.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3")),
        ),
    },
    query_cabinets=("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3"),
    query_target_cabinets={case_id: ("cabinet.n.01_1",) for case_id in ("A", "B", "C")},
    entry_checks={
        "key_1": (
            ("ontop", ("mug.n.04_1", "countertop.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3")),
        ),
        "query": (
            ("inside", ("mug.n.04_1", "cabinet.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3")),
        ),
    },
)
