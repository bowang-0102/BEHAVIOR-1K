from memory_bench.tasks.base_task import MemoryTask

_OBJECTS = (
    "hamburger.n.01_1",
    "countertop.n.01_1",
    "countertop.n.01_2",
    "countertop.n.01_3",
    "microwave.n.02_1",
)
_VARIANTS = ("A1", "A2")


TASK = MemoryTask(
    task_name="mb006_retrieve_hamburger_from_microwave",
    scene_model="house_single_floor",
    room_types=("kitchen",),
    whitelist={
        "hamburger.n.01": {"hamburger": {"fyswch": None}},
        "countertop.n.01": {
            "countertop": {"kelker": None, "kelzer": None},
            "bar": {"udatjt": None},
        },
        "microwave.n.02": {"microwave": {"abzvij": None}},
    },
    key_activities=("mb_place_hamburger_on_bar", "mb_put_hamburger_into_microwave"),
    intervent_activities=("organizing_school_stuff", "picking_up_toys"),
    query_activity="mb_retrieve_hamburger_from_microwave",
    query_instruction=(
        "Retrieve the hamburger from the microwave and return it to the counter you originally took it from, "
        "then close the door."
    ),
    key_instructions={
        variant: {
            "mb_place_hamburger_on_bar": (
                "Place the hamburger from the kitchen counter on the bar, keeping the microwave door closed."
            ),
            "mb_put_hamburger_into_microwave": (
                "Open the microwave, put the hamburger from the bar into it, and close the door."
            ),
        }
        for variant in _VARIANTS
    },
    # Counter role 1 is the historical source; role 3 is always the preparation bar.
    variants={
        variant: {
            "countertop.n.01_1": source_counter,
            "countertop.n.01_2": other_counter,
            "countertop.n.01_3": "bar_udatjt_0",
            "microwave.n.02_1": "microwave_abzvij_0",
        }
        for variant, source_counter, other_counter in (
            ("A1", "countertop_kelker_0", "countertop_kelzer_0"),
            ("A2", "countertop_kelzer_0", "countertop_kelker_0"),
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
            ("mb_place_hamburger_on_bar", ("hamburger.n.01_1",)),
            ("mb_put_hamburger_into_microwave", ("hamburger.n.01_1", "microwave.n.02_1")),
            ("mb_retrieve_hamburger_from_microwave", ("hamburger.n.01_1", "microwave.n.02_1")),
        )
    },
    init={
        variant: {
            "key_1": [
                ["ontop", "hamburger.n.01_1", "countertop.n.01_1"],
                ["not", ["open", "microwave.n.02_1"]],
            ],
            "key_2": [
                ["ontop", "hamburger.n.01_1", "countertop.n.01_3"],
                ["not", ["open", "microwave.n.02_1"]],
            ],
            "query": [
                ["inside", "hamburger.n.01_1", "microwave.n.02_1"],
                ["not", ["open", "microwave.n.02_1"]],
            ],
        }
        for variant in _VARIANTS
    },
    goal={
        variant: {
            "key_1": [
                ["ontop", "hamburger.n.01_1", "countertop.n.01_3"],
                ["not", ["open", "microwave.n.02_1"]],
            ],
            "key_2": [
                ["inside", "hamburger.n.01_1", "microwave.n.02_1"],
                ["not", ["open", "microwave.n.02_1"]],
            ],
            "query": [
                ["ontop", "hamburger.n.01_1", "countertop.n.01_1"],
                ["not", ["open", "microwave.n.02_1"]],
            ],
        }
        for variant in _VARIANTS
    },
)
