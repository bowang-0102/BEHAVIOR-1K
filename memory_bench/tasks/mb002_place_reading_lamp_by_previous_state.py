from memory_bench.tasks.base_task import MemoryTask

_VARIANTS = ("A", "B")


TASK = MemoryTask(
    task_name="mb002_place_reading_lamp_by_previous_state",
    scene_model="house_double_floor_upper",
    room_types=("bedroom",),
    whitelist={
        "table_lamp.n.01": {"table_lamp": {"wbcgev": 0.9}},
        "desk.n.01": {"desk": {"aduafr": None}},
        "bed.n.01": {"bed": {"klcjcy": None}},
        "bookcase.n.01": {"bookcase": {"kdnyfh": None}},
    },
    key_activities=("mb_move_reading_lamp_to_bed",),
    intervent_activities=("re_shelving_library_books", "getting_organized_for_work"),
    query_activity="mb_place_reading_lamp_by_previous_state",
    query_instruction=(
        "If the reading lamp was on when you first found it on the desk, return it to the desk and turn it on. "
        "If it was off, put it away on top of the low bookcase and leave it off."
    ),
    key_instructions={
        variant: {
            "mb_move_reading_lamp_to_bed": "Clear the desk by moving the reading lamp to the bed, leaving it off."
        }
        for variant in _VARIANTS
    },
    # Bindings stay identical: A/B encode the same lamp's historical state.
    variants={
        variant: {
            "table_lamp.n.01_1": "reading_lamp",
            "desk.n.01_1": "desk_aduafr_0",
            "bed.n.01_1": "bed_klcjcy_0",
            "bookcase.n.01_1": "bookcase_kdnyfh_0",
        }
        for variant in _VARIANTS
    },
    variant_object_models={"reading_lamp": ("table_lamp", "wbcgev")},
    object_usage={
        activity: {
            "used": ("table_lamp.n.01_1", "desk.n.01_1", "bed.n.01_1", "bookcase.n.01_1"),
            "read": ("table_lamp.n.01_1", "desk.n.01_1", "bed.n.01_1", "bookcase.n.01_1"),
            "write": ("table_lamp.n.01_1",),
            "protected": ("table_lamp.n.01_1", "desk.n.01_1", "bed.n.01_1", "bookcase.n.01_1"),
        }
        for activity in ("mb_move_reading_lamp_to_bed", "mb_place_reading_lamp_by_previous_state")
    },
    init={
        "A": {
            "key_1": [
                ["ontop", "table_lamp.n.01_1", "desk.n.01_1"],
                ["toggled_on", "table_lamp.n.01_1"],
            ],
            "query": [
                ["ontop", "table_lamp.n.01_1", "bed.n.01_1"],
                ["not", ["toggled_on", "table_lamp.n.01_1"]],
            ],
        },
        "B": {
            "key_1": [
                ["ontop", "table_lamp.n.01_1", "desk.n.01_1"],
                ["not", ["toggled_on", "table_lamp.n.01_1"]],
            ],
            "query": [
                ["ontop", "table_lamp.n.01_1", "bed.n.01_1"],
                ["not", ["toggled_on", "table_lamp.n.01_1"]],
            ],
        },
    },
    goal={
        "A": {
            "key_1": [
                ["ontop", "table_lamp.n.01_1", "bed.n.01_1"],
                ["not", ["toggled_on", "table_lamp.n.01_1"]],
            ],
            "query": [
                ["ontop", "table_lamp.n.01_1", "desk.n.01_1"],
                ["toggled_on", "table_lamp.n.01_1"],
            ],
        },
        "B": {
            "key_1": [
                ["ontop", "table_lamp.n.01_1", "bed.n.01_1"],
                ["not", ["toggled_on", "table_lamp.n.01_1"]],
            ],
            "query": [
                ["ontop", "table_lamp.n.01_1", "bookcase.n.01_1"],
                ["not", ["toggled_on", "table_lamp.n.01_1"]],
            ],
        },
    },
)
