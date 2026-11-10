from memory_bench.tasks.base_task import MemoryTask

_OBJECTS = (
    "bowl.n.01_1",
    "vessel.n.03_1",
    "chopping_board.n.01_1",
    "countertop.n.01_1",
    "countertop.n.01_2",
    "countertop.n.01_3",
    "shelf.n.01_1",
    "shelf.n.01_2",
)
_VARIANTS = ("AS1", "AS2", "BS1", "BS2")


TASK = MemoryTask(
    task_name="mb005_returning_meal_prep_supplies",
    scene_model="house_single_floor",
    room_types=("kitchen",),
    # Separate synsets fix the two bowl models independently during sampling.
    whitelist={
        "bowl.n.01": {"bowl": {"hpqjug": None}},
        "vessel.n.03": {"bowl": {"npuuir": None}},
        "chopping_board.n.01": {"cutting_board": {"aibvew": None}},
        "countertop.n.01": {"countertop": {"kelker": None, "kelzer": None}, "bar": {"udatjt": None}},
        "shelf.n.01": {"shelf": {"pfusrd": None}},
    },
    key_activities=("mb_taking_bowl_to_prep_counter", "mb_taking_cutting_board_to_prep_counter"),
    intervent_activities=("organizing_school_stuff", "picking_up_toys"),
    query_activity="mb_returning_meal_prep_supplies",
    query_instruction=(
        "We are not preparing the meal after all. Return the bowl you brought over to the lower shelf, "
        "and return the cutting board to the counter you took it from. Leave the other bowl on the bar."
    ),
    key_instructions={
        variant: {
            "mb_taking_bowl_to_prep_counter": (
                "Bring the bowl from the lower shelf to the bar. "
                "Place it side by side with the bowl already there, without stacking."
            ),
            "mb_taking_cutting_board_to_prep_counter": (
                "Bring the cutting board from the counter to the bar. "
                "Place it separately beside the two bowls and leave both bowls where they are."
            ),
        }
        for variant in _VARIANTS
    },
    # Countertop role 1 is the bar; role 2 is the board's historical source.
    # Both shelf bindings retain the lower shelf in every template. No task object uses the upper shelf.
    variants={
        variant: {
            "bowl.n.01_1": target_bowl,
            "vessel.n.03_1": other_bowl,
            "countertop.n.01_1": "bar_udatjt_0",
            "countertop.n.01_2": source_support,
            "countertop.n.01_3": other_support,
            "shelf.n.01_1": "shelf_pfusrd_1",
            "shelf.n.01_2": "shelf_pfusrd_0",
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
            ("mb_taking_bowl_to_prep_counter", ("bowl.n.01_1",)),
            ("mb_taking_cutting_board_to_prep_counter", ("chopping_board.n.01_1",)),
            ("mb_returning_meal_prep_supplies", ("bowl.n.01_1", "chopping_board.n.01_1")),
        )
    },
    init={
        variant: {
            "key_1": [
                ["ontop", "bowl.n.01_1", "shelf.n.01_1"],
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
                ["ontop", "bowl.n.01_1", "shelf.n.01_1"],
                ["ontop", "vessel.n.03_1", "countertop.n.01_1"],
                ["ontop", "chopping_board.n.01_1", "countertop.n.01_2"],
            ],
        }
        for variant in _VARIANTS
    },
)
