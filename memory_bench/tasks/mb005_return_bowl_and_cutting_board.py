from memory_bench.tasks.base_task import MemoryTask

_OBJECTS = (
    "bowl.n.01_1",
    "vessel.n.03_1",
    "chopping_board.n.01_1",
    "countertop.n.01_1",
    "countertop.n.01_2",
    "countertop.n.01_3",
    "microwave.n.02_1",
)
_VARIANTS = ("AS1", "AS2", "BS1", "BS2")


TASK = MemoryTask(
    task_name="mb005_return_bowl_and_cutting_board",
    scene_model="house_single_floor",
    room_types=("kitchen",),
    # Separate synsets fix the two bowl models independently during sampling.
    whitelist={
        "bowl.n.01": {"bowl": {"hpqjug": None}},
        "vessel.n.03": {"bowl": {"npuuir": None}},
        "chopping_board.n.01": {"cutting_board": {"aibvew": None}},
        "countertop.n.01": {"countertop": {"kelker": None, "kelzer": None}, "bar": {"udatjt": None}},
        "microwave.n.02": {"microwave": {"abzvij": None}},
    },
    key_activities=("mb_place_bowl_on_bar", "mb_place_cutting_board_on_bar"),
    intervent_activities=("putting_away_toys", "bringing_water"),
    query_activity="mb_return_bowl_and_cutting_board",
    query_instruction=(
        "Return the bowl you brought over to the top of the microwave "
        "and the cutting board to the counter you took it from, "
        "leaving the other bowl on the bar."
    ),
    key_instructions={
        variant: {
            "mb_place_bowl_on_bar": (
                "Place the bowl from the top of the microwave on the bar "
                "beside the bowl already there without stacking them."
            ),
            "mb_place_cutting_board_on_bar": (
                "Place the cutting board from the counter on the bar beside the two bowls, "
                "keeping it separate and leaving both bowls in place."
            ),
        }
        for variant in _VARIANTS
    },
    # Countertop role 1 is the bar; role 2 is the board's historical source.
    # The bowl's source and return destination is the top of the microwave in every variant.
    variants={
        variant: {
            "bowl.n.01_1": target_bowl,
            "vessel.n.03_1": other_bowl,
            "countertop.n.01_1": "bar_udatjt_0",
            "countertop.n.01_2": source_support,
            "countertop.n.01_3": other_support,
            "microwave.n.02_1": "microwave_abzvij_0",
        }
        for variant, target_bowl, other_bowl, source_support, other_support in (
            ("AS1", "bowl_a", "bowl_b", "countertop_kelker_0", "countertop_kelzer_0"),
            ("AS2", "bowl_a", "bowl_b", "countertop_kelzer_0", "countertop_kelker_0"),
            ("BS1", "bowl_b", "bowl_a", "countertop_kelker_0", "countertop_kelzer_0"),
            ("BS2", "bowl_b", "bowl_a", "countertop_kelzer_0", "countertop_kelker_0"),
        )
    },
    variant_object_models={
        "bowl_a": ("bowl", "hpqjug"),
        "bowl_b": ("bowl", "npuuir"),
    },
    object_usage={
        activity: {
            "used": _OBJECTS,
            "read": _OBJECTS,
            "write": writes,
            "protected": _OBJECTS,
        }
        for activity, writes in (
            ("mb_place_bowl_on_bar", ("bowl.n.01_1",)),
            ("mb_place_cutting_board_on_bar", ("chopping_board.n.01_1",)),
            ("mb_return_bowl_and_cutting_board", ("bowl.n.01_1", "chopping_board.n.01_1")),
        )
    },
    init={
        variant: {
            "key_1": [
                ["ontop", "bowl.n.01_1", "microwave.n.02_1"],
                ["ontop", "vessel.n.03_1", "countertop.n.01_1"],
                ["ontop", "chopping_board.n.01_1", "countertop.n.01_2"],
            ],
            "key_2": [
                ["ontop", "bowl.n.01_1", "countertop.n.01_1"],
                ["ontop", "vessel.n.03_1", "countertop.n.01_1"],
                ["ontop", "chopping_board.n.01_1", "countertop.n.01_2"],
            ],
            "query": [
                ["ontop", "bowl.n.01_1", "countertop.n.01_1"],
                ["ontop", "vessel.n.03_1", "countertop.n.01_1"],
                ["ontop", "chopping_board.n.01_1", "countertop.n.01_1"],
            ],
        }
        for variant in _VARIANTS
    },
    goal={
        variant: {
            "key_1": [
                ["ontop", "bowl.n.01_1", "countertop.n.01_1"],
                ["ontop", "vessel.n.03_1", "countertop.n.01_1"],
                ["ontop", "chopping_board.n.01_1", "countertop.n.01_2"],
            ],
            "key_2": [
                ["ontop", "bowl.n.01_1", "countertop.n.01_1"],
                ["ontop", "vessel.n.03_1", "countertop.n.01_1"],
                ["ontop", "chopping_board.n.01_1", "countertop.n.01_1"],
            ],
            "query": [
                ["ontop", "bowl.n.01_1", "microwave.n.02_1"],
                ["ontop", "vessel.n.03_1", "countertop.n.01_1"],
                ["ontop", "chopping_board.n.01_1", "countertop.n.01_2"],
            ],
        }
        for variant in _VARIANTS
    },
)
