from memory_bench.tasks.base_task import MemoryTask

_VARIANTS = ("AB", "AC", "BA", "BC", "CA", "CB")
_OBJECTS = (
    "bratwurst.n.01_1",
    "hinged_jar.n.01_1",
    "chopping_board.n.01_1",
    "countertop.n.01_1",
    "cabinet.n.01_1",
    "cabinet.n.01_2",
    "cabinet.n.01_3",
)
_CABINETS = {
    "A": ("top_cabinet_lkxmne_2", "the wall cabinet nearest the microwave"),
    "B": ("top_cabinet_lkxmne_1", "the middle wall cabinet"),
    "C": ("top_cabinet_lkxmne_0", "the wall cabinet farthest from the microwave"),
}
_CLOSED = [
    ["not", ["open", "hinged_jar.n.01_1"]],
    ["not", ["open", "cabinet.n.01_1"]],
    ["not", ["open", "cabinet.n.01_2"]],
    ["not", ["open", "cabinet.n.01_3"]],
]
_INITIAL = [
    ["inside", "hinged_jar.n.01_1", "cabinet.n.01_1"],
    ["ontop", "chopping_board.n.01_1", "countertop.n.01_1"],
    ["ontop", "bratwurst.n.01_1", "chopping_board.n.01_1"],
    ["cooked", "bratwurst.n.01_1"],
    *_CLOSED,
]
_PACKED = [
    ["inside", "bratwurst.n.01_1", "hinged_jar.n.01_1"],
    ["cooked", "bratwurst.n.01_1"],
    ["ontop", "chopping_board.n.01_1", "countertop.n.01_1"],
    *_CLOSED,
]
_IN_OLD_CABINET = [
    ["inside", "hinged_jar.n.01_1", "cabinet.n.01_1"],
    ["not", ["inside", "hinged_jar.n.01_1", "cabinet.n.01_2"]],
    ["not", ["inside", "hinged_jar.n.01_1", "cabinet.n.01_3"]],
    *_PACKED,
]
_IN_NEW_CABINET = [
    ["inside", "hinged_jar.n.01_1", "cabinet.n.01_2"],
    ["not", ["inside", "hinged_jar.n.01_1", "cabinet.n.01_1"]],
    ["not", ["inside", "hinged_jar.n.01_1", "cabinet.n.01_3"]],
    *_PACKED,
]
_ON_BAR = [
    ["ontop", "hinged_jar.n.01_1", "countertop.n.01_1"],
    ["not", ["inside", "hinged_jar.n.01_1", "cabinet.n.01_1"]],
    ["not", ["inside", "hinged_jar.n.01_1", "cabinet.n.01_2"]],
    ["not", ["inside", "hinged_jar.n.01_1", "cabinet.n.01_3"]],
    *_PACKED,
]


TASK = MemoryTask(
    task_name="mb006_retrieve_relocated_meat_jar",
    scene_model="house_single_floor",
    room_types=("kitchen",),
    # Reuse can_meat's models and dimensions, with one jar and one cooked sausage.
    whitelist={
        "hinged_jar.n.01": {"hinged_jar": {"vzwhbg": 0.9}},
        "bratwurst.n.01": {"bratwurst": {"pqfrrn": [0.1148, 0.0570, 0.0203]}},
        "chopping_board.n.01": {"cutting_board": {"jfrbuz": [0.5141, 0.3012, 0.0254]}},
        "countertop.n.01": {"bar": {"udatjt": None}},
        "cabinet.n.01": {"top_cabinet": {"lkxmne": None}},
    },
    key_activities=("mb_can_and_store_meat", "mb_relocate_meat_jar"),
    intervent_activities=("putting_away_toys", "bringing_water"),
    query_activity="mb_retrieve_relocated_meat_jar",
    query_instruction=(
        "Retrieve the jar of meat you stored earlier and place it on the bar, "
        "keeping the jar closed and the sausage inside, then close all the cabinet doors."
    ),
    key_instructions={
        variant: {
            "mb_can_and_store_meat": (
                "Take the empty jar from the wall cabinet, put the cooked sausage from the chopping board "
                f"into it, close the jar, and store it in {_CABINETS[variant[0]][1]}. "
                "Close all the cabinet doors."
            ),
            "mb_relocate_meat_jar": (
                f"Move the sealed jar of meat to {_CABINETS[variant[1]][1]}, "
                "keeping the jar closed and the sausage inside, then close all the cabinet doors."
            ),
        }
        for variant in _VARIANTS
    },
    # Roles 1 and 2 are the old and new cabinets; role 3 is the unused candidate.
    # The first Key takes the empty jar from role 1 and returns it there after filling.
    variants={
        variant: {
            "cabinet.n.01_1": _CABINETS[variant[0]][0],
            "cabinet.n.01_2": _CABINETS[variant[1]][0],
            "cabinet.n.01_3": next(value[0] for label, value in _CABINETS.items() if label not in variant),
            "countertop.n.01_1": "bar_udatjt_0",
        }
        for variant in _VARIANTS
    },
    object_usage={
        activity: {
            "used": _OBJECTS,
            "read": _OBJECTS,
            # The sausage moves physically with its jar in Key 2 and Query.
            "write": (
                "bratwurst.n.01_1",
                "hinged_jar.n.01_1",
                "cabinet.n.01_1",
                "cabinet.n.01_2",
                "cabinet.n.01_3",
            ),
            "protected": _OBJECTS,
        }
        for activity in ("mb_can_and_store_meat", "mb_relocate_meat_jar", "mb_retrieve_relocated_meat_jar")
    },
    init={
        variant: {"key_1": _INITIAL, "key_2": _IN_OLD_CABINET, "query": _IN_NEW_CABINET}
        for variant in _VARIANTS
    },
    goal={
        variant: {"key_1": _IN_OLD_CABINET, "key_2": _IN_NEW_CABINET, "query": _ON_BAR}
        for variant in _VARIANTS
    },
)
