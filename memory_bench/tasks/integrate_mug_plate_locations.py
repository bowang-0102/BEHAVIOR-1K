"""Level 2 information integration across independent mug and plate Keys."""

from memory_bench.tasks.base_task import MemoryTask


_MUG_CABINETS = tuple(f"top_cabinet_lkxmne_{index}" for index in range(3))
_PLATE_CABINETS = (
    "bottom_cabinet_fancyy_0",
    "bottom_cabinet_no_top_gjeoer_0",
    "bottom_cabinet_no_top_rkgjer_0",
)
_CASES = {
    f"mug_{mug_index}_plate_{plate_index}": (mug_index, plate_index)
    for mug_index in range(3)
    for plate_index in range(3)
}


def _activity_variants() -> dict[str, dict[str, dict[str, str]]]:
    variants = {}
    for case_id, (mug_target, plate_target) in _CASES.items():
        mug_rest = [name for index, name in enumerate(_MUG_CABINETS) if index != mug_target]
        plate_rest = [name for index, name in enumerate(_PLATE_CABINETS) if index != plate_target]
        variants[case_id] = {
            "mb_store_mug_for_later": {
                "cabinet.n.01_1": _MUG_CABINETS[mug_target],
                "cabinet.n.01_2": mug_rest[0],
                "cabinet.n.01_3": mug_rest[1],
            },
            "mb_store_plate_for_later": {
                "cabinet.n.01_1": _PLATE_CABINETS[plate_target],
                "cabinet.n.01_2": plate_rest[0],
                "cabinet.n.01_3": plate_rest[1],
            },
            "mb_retrieve_mug_and_plate": {
                "cabinet.n.01_1": _MUG_CABINETS[mug_target],
                "cabinet.n.01_2": mug_rest[0],
                "cabinet.n.01_3": mug_rest[1],
                "cabinet.n.01_4": _PLATE_CABINETS[plate_target],
                "cabinet.n.01_5": plate_rest[0],
                "cabinet.n.01_6": plate_rest[1],
            },
        }
    return variants


TASK = MemoryTask(
    task_name="mb_retrieve_mug_and_plate",
    scene_model="house_single_floor",
    key_activities=("mb_store_mug_for_later", "mb_store_plate_for_later"),
    intervent_activities=("tidying_bedroom", "outfit_a_basic_toolbox"),
    query_activity="mb_retrieve_mug_and_plate",
    query_instruction="Retrieve the mug and the plate and place them on the kitchen island.",
    room_types=("kitchen",),
    whitelist={
        "mug.n.04": {"mug": {"ntgftr": None}},
        "plate.n.04": {"plate": {"luhkiz": None}},
        "cabinet.n.01": {
            "top_cabinet": {"lkxmne": None},
            "bottom_cabinet": {"fancyy": None},
            "bottom_cabinet_no_top": {"gjeoer": None, "rkgjer": None},
        },
        "countertop.n.01": {"bar": {"udatjt": None}},
    },
    variants={case_id: {} for case_id in _CASES},
    segment_instructions={
        "mb_store_mug_for_later": "Put the mug from the kitchen island into a top cabinet and close the door.",
        "mb_store_plate_for_later": "Put the plate from the kitchen island into a bottom cabinet and close the door.",
        "mb_retrieve_mug_and_plate": "Retrieve the mug and the plate and place them on the kitchen island.",
    },
    key_information={
        "key_1": {
            "provides": "The selected cabinet containing the mug.",
            "bindings": {"mug.n.04_1": "mug", "cabinet.n.01_1": "mug_cabinet"},
            "updates": {},
        },
        "key_2": {
            "provides": "The selected cabinet containing the plate.",
            "bindings": {"plate.n.04_1": "plate", "cabinet.n.01_1": "plate_cabinet"},
            "updates": {},
        },
    },
    key_dependencies={"key_1": (), "key_2": ()},
    object_usage={
        "mb_store_mug_for_later": {
            "used": ("mug.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3", "countertop.n.01_1"),
            "read": ("mug.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3", "countertop.n.01_1"),
            "write": ("mug.n.04_1", "cabinet.n.01_1"),
            "protected": ("mug.n.04_1", "plate.n.04_1", *tuple(f"cabinet.n.01_{index}" for index in range(1, 7))),
        },
        "mb_store_plate_for_later": {
            "used": ("plate.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3", "countertop.n.01_1"),
            "read": ("plate.n.04_1", "cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3", "countertop.n.01_1"),
            "write": ("plate.n.04_1", "cabinet.n.01_1"),
            "protected": ("mug.n.04_1", "plate.n.04_1", *tuple(f"cabinet.n.01_{index}" for index in range(1, 7))),
        },
        "mb_retrieve_mug_and_plate": {
            "used": (
                "mug.n.04_1",
                "plate.n.04_1",
                *(f"cabinet.n.01_{index}" for index in range(1, 7)),
                "countertop.n.01_1",
            ),
            "read": (
                "mug.n.04_1",
                "plate.n.04_1",
                *(f"cabinet.n.01_{index}" for index in range(1, 7)),
            ),
            "write": ("mug.n.04_1", "plate.n.04_1", "cabinet.n.01_1", "cabinet.n.01_4"),
            "protected": ("mug.n.04_1", "plate.n.04_1", *(f"cabinet.n.01_{index}" for index in range(1, 7))),
        },
    },
    segment_checks={
        "key_1": (
            ("inside", ("mug.n.04_1", "cabinet.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3")),
        ),
        "key_2": (
            ("inside", ("plate.n.04_1", "cabinet.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3")),
        ),
        "query": (
            ("ontop", ("mug.n.04_1", "countertop.n.01_1")),
            ("ontop", ("plate.n.04_1", "countertop.n.01_1")),
            ("all_closed", tuple(f"cabinet.n.01_{index}" for index in range(1, 7))),
        ),
    },
    query_cabinets=tuple(f"cabinet.n.01_{index}" for index in range(1, 7)),
    query_target_cabinets={case_id: ("cabinet.n.01_1", "cabinet.n.01_4") for case_id in _CASES},
    activity_variants=_activity_variants(),
    activity_whitelists={
        "mb_store_mug_for_later": {
            "mug.n.04": {"mug": {"ntgftr": None}},
            "cabinet.n.01": {"top_cabinet": {"lkxmne": None}},
            "countertop.n.01": {"bar": {"udatjt": None}},
        },
        "mb_store_plate_for_later": {
            "plate.n.04": {"plate": {"luhkiz": None}},
            "cabinet.n.01": {
                "bottom_cabinet": {"fancyy": None},
                "bottom_cabinet_no_top": {"gjeoer": None, "rkgjer": None},
            },
            "countertop.n.01": {"bar": {"udatjt": None}},
        },
        "mb_retrieve_mug_and_plate": {
            "mug.n.04": {"mug": {"ntgftr": None}},
            "plate.n.04": {"plate": {"luhkiz": None}},
            "cabinet.n.01": {
                "top_cabinet": {"lkxmne": None},
                "bottom_cabinet": {"fancyy": None},
                "bottom_cabinet_no_top": {"gjeoer": None, "rkgjer": None},
            },
            "countertop.n.01": {"bar": {"udatjt": None}},
        },
    },
    entry_checks={
        "key_1": (
            ("ontop", ("mug.n.04_1", "countertop.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3")),
        ),
        "key_2": (
            ("ontop", ("plate.n.04_1", "countertop.n.01_1")),
            ("all_closed", ("cabinet.n.01_1", "cabinet.n.01_2", "cabinet.n.01_3")),
        ),
        "query": (
            ("inside", ("mug.n.04_1", "cabinet.n.01_1")),
            ("inside", ("plate.n.04_1", "cabinet.n.01_4")),
            ("all_closed", tuple(f"cabinet.n.01_{index}" for index in range(1, 7))),
        ),
    },
)
