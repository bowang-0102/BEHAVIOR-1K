from memory_bench.tasks.base_task import MemoryTask


# Conditions use fixed physical roles; only the remembered facts vary.
VARIANT_INIT = {
    "A": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "bookcase.n.01_1"]],
        "query": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"]],
    },
    "B": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "bookcase.n.01_2"]],
        "query": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"]],
    },
}

VARIANT_GOAL = {
    "A": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"]],
        "query": [["ontop", "coffee_cup.n.01_1", "bookcase.n.01_1"]],
    },
    "B": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "desk.n.01_1"]],
        "query": [["ontop", "coffee_cup.n.01_1", "bookcase.n.01_2"]],
    },
}


TASK = MemoryTask(
    task_name="mb_return_borrowed_cup",
    scene_model="house_double_floor_upper",
    room_types=("living_room",),
    whitelist={
        "bookcase.n.01": {"bookcase": {"otwukr": None}},
        "desk.n.01": {"desk": {"iotfzl": None}},
        "coffee_cup.n.01": {"coffee_cup": {"nbhcgu": None}},
    },
    key_activities=("mb_borrow_living_room_cup",),
    intervent_activities=("organizing_art_supplies", "setting_mousetraps"),
    query_activity="mb_return_borrowed_cup",
    query_instruction="Return the cup borrowed from the bookcase top to its original bookcase top.",
    key_instructions={
        "A": {
            "mb_borrow_living_room_cup": "Bring the cup from the bookcase top to the clear part of the desk for later use.",
        },
        "B": {
            "mb_borrow_living_room_cup": "Bring the cup from the bookcase top to the clear part of the desk for later use.",
        },
    },
    variants={"A": {}, "B": {}},
    object_usage={
        "mb_borrow_living_room_cup": {
            "used": ("bookcase.n.01_1", "bookcase.n.01_2", "desk.n.01_1", "coffee_cup.n.01_1"),
            "read": ("bookcase.n.01_1", "bookcase.n.01_2", "desk.n.01_1", "coffee_cup.n.01_1"),
            "write": ("coffee_cup.n.01_1",),
            "protected": ("bookcase.n.01_1", "bookcase.n.01_2", "desk.n.01_1", "coffee_cup.n.01_1"),
        },
        "mb_return_borrowed_cup": {
            "used": ("bookcase.n.01_1", "bookcase.n.01_2", "desk.n.01_1", "coffee_cup.n.01_1"),
            "read": ("bookcase.n.01_1", "bookcase.n.01_2", "desk.n.01_1", "coffee_cup.n.01_1"),
            "write": ("coffee_cup.n.01_1",),
            "protected": ("bookcase.n.01_1", "bookcase.n.01_2", "desk.n.01_1", "coffee_cup.n.01_1"),
        },
    },
    scene_bindings={
        "bookcase.n.01_1": "bookcase_otwukr_0",
        "bookcase.n.01_2": "bookcase_otwukr_3",
        "desk.n.01_1": "desk_iotfzl_0",
    },
    top_surfaces=("bookcase.n.01_1", "bookcase.n.01_2", "desk.n.01_1"),
    goal_regions={
        "A": {"key_1": {"coffee_cup.n.01_1": ("desk.n.01_1", 0.2, 0.15, 0.8, 0.45)}},
        "B": {"key_1": {"coffee_cup.n.01_1": ("desk.n.01_1", 0.2, 0.15, 0.8, 0.45)}},
    },
    init=VARIANT_INIT['A'],
    goal=VARIANT_GOAL['A'],
    variant_init=VARIANT_INIT,
    variant_goal=VARIANT_GOAL,
    initial_states={variant: conditions["key_1"] for variant, conditions in VARIANT_INIT.items()},
)
