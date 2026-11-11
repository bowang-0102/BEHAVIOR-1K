from memory_bench.tasks.base_task import MemoryTask

TASK = MemoryTask(
    task_name="mb002_turn_on_previously_used_reading_lamp",
    scene_model="house_double_floor_upper",
    room_types=("bedroom",),
    # Use a parent lamp synset for the second table lamp so each inserted
    # model is fixed independently and has a unique cross-segment identity.
    whitelist={
        "table_lamp.n.01": {"table_lamp": {"fhsorf": None}},
        "lamp.n.02": {"table_lamp": {"wbcgev": None}},
        "desk.n.01": {"desk": {"uqcmzf": None}},
    },
    key_activities=("mb_turn_off_reading_lamps",),
    intervent_activities=("re_shelving_library_books", "organizing_art_supplies"),
    query_activity="mb_turn_on_previously_used_reading_lamp",
    query_instruction=(
        "Turn on only the reading lamp on the desk that you were using earlier."
    ),
    key_instructions={
        variant: {
            "mb_turn_off_reading_lamps": "Turn off both reading lamps on the desk."
        }
        for variant in ("A", "B")
    },
    # Logical role table_lamp.n.01_1 always denotes the previously lit lamp.
    # Model identity and desk placement are independent of that historical state.
    variants={
        "A": {
            "table_lamp.n.01_1": "reading_lamp_a",
            "lamp.n.02_1": "reading_lamp_b",
            "desk.n.01_1": "desk_uqcmzf_0",
        },
        "B": {
            "table_lamp.n.01_1": "reading_lamp_b",
            "lamp.n.02_1": "reading_lamp_a",
            "desk.n.01_1": "desk_uqcmzf_0",
        },
    },
    variant_object_models={
        "reading_lamp_a": ("table_lamp", "fhsorf"),
        "reading_lamp_b": ("table_lamp", "wbcgev"),
    },
    object_usage={
        activity: {
            "used": ("table_lamp.n.01_1", "lamp.n.02_1", "desk.n.01_1"),
            "read": ("table_lamp.n.01_1", "lamp.n.02_1", "desk.n.01_1"),
            "write": ("table_lamp.n.01_1", "lamp.n.02_1"),
            "protected": ("table_lamp.n.01_1", "lamp.n.02_1", "desk.n.01_1"),
        }
        for activity in ("mb_turn_off_reading_lamps", "mb_turn_on_previously_used_reading_lamp")
    },
    init={
        variant: {
            "key_1": [
                ["ontop", "table_lamp.n.01_1", "desk.n.01_1"],
                ["ontop", "lamp.n.02_1", "desk.n.01_1"],
                ["toggled_on", "table_lamp.n.01_1"],
                ["not", ["toggled_on", "lamp.n.02_1"]],
            ],
            "query": [
                ["ontop", "table_lamp.n.01_1", "desk.n.01_1"],
                ["ontop", "lamp.n.02_1", "desk.n.01_1"],
                ["not", ["toggled_on", "table_lamp.n.01_1"]],
                ["not", ["toggled_on", "lamp.n.02_1"]],
            ],
        }
        for variant in ("A", "B")
    },
    goal={
        variant: {
            "key_1": [
                ["ontop", "table_lamp.n.01_1", "desk.n.01_1"],
                ["ontop", "lamp.n.02_1", "desk.n.01_1"],
                ["not", ["toggled_on", "table_lamp.n.01_1"]],
                ["not", ["toggled_on", "lamp.n.02_1"]],
            ],
            "query": [
                ["ontop", "table_lamp.n.01_1", "desk.n.01_1"],
                ["ontop", "lamp.n.02_1", "desk.n.01_1"],
                ["toggled_on", "table_lamp.n.01_1"],
                ["not", ["toggled_on", "lamp.n.02_1"]],
            ],
        }
        for variant in ("A", "B")
    },
)
