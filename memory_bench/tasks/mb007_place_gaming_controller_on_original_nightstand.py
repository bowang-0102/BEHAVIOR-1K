from memory_bench.tasks.base_task import MemoryTask

_OBJECTS = (
    "gaming_controller.n.01_1",
    "toy_box.n.01_1",
    "stand.n.04_1",
    "stand.n.04_2",
    "bed.n.01_1",
    "footstool.n.01_1",
)
_VARIANTS = ("A", "B")

_INITIAL = [
    ["ontop", "gaming_controller.n.01_1", "stand.n.04_1"],
    ["not", ["ontop", "gaming_controller.n.01_1", "stand.n.04_2"]],
    ["not", ["inside", "gaming_controller.n.01_1", "toy_box.n.01_1"]],
    ["ontop", "toy_box.n.01_1", "bed.n.01_1"],
    ["not", ["ontop", "toy_box.n.01_1", "footstool.n.01_1"]],
    ["not", ["ontop", "gaming_controller.n.01_1", "footstool.n.01_1"]],
]
_IN_BOX_ON_BED = [
    ["inside", "gaming_controller.n.01_1", "toy_box.n.01_1"],
    ["ontop", "toy_box.n.01_1", "bed.n.01_1"],
    ["not", ["ontop", "gaming_controller.n.01_1", "stand.n.04_1"]],
    ["not", ["ontop", "gaming_controller.n.01_1", "stand.n.04_2"]],
]
_IN_BOX_ON_OTTOMAN = [
    ["inside", "gaming_controller.n.01_1", "toy_box.n.01_1"],
    ["ontop", "toy_box.n.01_1", "footstool.n.01_1"],
    ["not", ["ontop", "toy_box.n.01_1", "bed.n.01_1"]],
    ["not", ["ontop", "gaming_controller.n.01_1", "stand.n.04_1"]],
    ["not", ["ontop", "gaming_controller.n.01_1", "stand.n.04_2"]],
]
_RETURNED = [
    ["ontop", "gaming_controller.n.01_1", "stand.n.04_1"],
    ["not", ["inside", "gaming_controller.n.01_1", "toy_box.n.01_1"]],
    ["ontop", "toy_box.n.01_1", "footstool.n.01_1"],
]


TASK = MemoryTask(
    task_name="mb007_place_gaming_controller_on_original_nightstand",
    scene_model="house_double_floor_upper",
    room_types=("bedroom",),
    whitelist={
        "gaming_controller.n.01": {"gaming_controller": {"yofqbq": None}},
        "toy_box.n.01": {"toy_box": {"aviadj": None}},
        "stand.n.04": {"nightstand": {"roxnbe": None}},
        "bed.n.01": {"bed": {"siuxgz": None}},
        "footstool.n.01": {"ottoman": {"qsnawv": None}},
    },
    key_activities=("mb_put_gaming_controller_into_storage_box", "mb_move_storage_box_to_footstool"),
    intervent_activities=("organizing_art_supplies", "re_shelving_library_books"),
    query_activity="mb_place_gaming_controller_on_original_nightstand",
    query_instruction=(
        "Retrieve the gaming controller from the storage box on the footstool and return it to the top "
        "of the nightstand you originally took it from, leaving the box on the footstool."
    ),
    key_instructions={
        variant: {
            "mb_put_gaming_controller_into_storage_box": (
                "Put the gaming controller from the top of the nightstand into the storage box on the bed."
            ),
            "mb_move_storage_box_to_footstool": (
                "Move the storage box from the bed to the footstool at the foot of the bed, "
                "keeping the gaming controller inside."
            ),
        }
        for variant in _VARIANTS
    },
    # Stand role 1 is the historical source; all furniture retains its scene identity.
    variants={
        variant: {
            "stand.n.04_1": source,
            "stand.n.04_2": other,
            "bed.n.01_1": "bed_siuxgz_0",
            "footstool.n.01_1": "ottoman_qsnawv_0",
        }
        for variant, source, other in (
            ("A", "nightstand_roxnbe_0", "nightstand_roxnbe_1"),
            ("B", "nightstand_roxnbe_1", "nightstand_roxnbe_0"),
        )
    },
    object_usage={
        activity: {
            "used": _OBJECTS,
            "read": _OBJECTS,
            "write": writes,
            "protected": _OBJECTS,
        }
        for activity, writes in (
            ("mb_put_gaming_controller_into_storage_box", ("gaming_controller.n.01_1",)),
            ("mb_move_storage_box_to_footstool", ("toy_box.n.01_1", "gaming_controller.n.01_1")),
            ("mb_place_gaming_controller_on_original_nightstand", ("gaming_controller.n.01_1",)),
        )
    },
    init={
        variant: {"key_1": _INITIAL, "key_2": _IN_BOX_ON_BED, "query": _IN_BOX_ON_OTTOMAN}
        for variant in _VARIANTS
    },
    goal={
        variant: {"key_1": _IN_BOX_ON_BED, "key_2": _IN_BOX_ON_OTTOMAN, "query": _RETURNED}
        for variant in _VARIANTS
    },
)
