from memory_bench.tasks.base_task import MemoryTask


# Conditions use fixed physical roles; only the remembered facts vary.
VARIANT_INIT = {
    "A_B": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_1"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
        "query": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_2"]],
    },
    "B_A": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_2"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
        "query": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_1"]],
    },
    "A_A": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_1"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
        "query": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_1"]],
    },
    "B_B": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_2"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
        "query": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_2"]],
    },
}

VARIANT_GOAL = {
    "A_B": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_2"]],
        "query": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
    },
    "B_A": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_1"]],
        "query": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
    },
    "A_A": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_1"]],
        "query": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
    },
    "B_B": {
        "key_1": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
        "key_2": [["ontop", "coffee_cup.n.01_1", "countertop.n.01_2"]],
        "query": [["ontop", "coffee_cup.n.01_1", "breakfast_table.n.01_1"]],
    },
}


TASK = MemoryTask(
    task_name="mb_retrieve_relocated_cup",
    scene_model="house_double_floor_lower",
    room_types=("kitchen",),
    whitelist={
        "countertop.n.01": {"bar": {"byvbuc": None, "egwapq": None}},
        "breakfast_table.n.01": {"breakfast_table": {"xftrki": None}},
        "coffee_cup.n.01": {"coffee_cup": {"nbhcgu": None}},
    },
    key_activities=("mb_bring_breakfast_cup", "mb_put_away_breakfast_cup"),
    intervent_activities=("turning_on_radio", "putting_shoes_on_rack"),
    query_activity="mb_retrieve_relocated_cup",
    query_instruction="Bring the cup you most recently put away back to the breakfast table for use.",
    key_instructions={
        "A_B": {
            "mb_bring_breakfast_cup": "Bring the cup from the kitchen counter to the breakfast table.",
            "mb_put_away_breakfast_cup": "Clear the breakfast table by putting the cup on the counter along the wall.",
        },
        "B_A": {
            "mb_bring_breakfast_cup": "Bring the cup from the kitchen counter to the breakfast table.",
            "mb_put_away_breakfast_cup": "Clear the breakfast table by putting the cup on the counter extending across the kitchen.",
        },
        "A_A": {
            "mb_bring_breakfast_cup": "Bring the cup from the kitchen counter to the breakfast table.",
            "mb_put_away_breakfast_cup": "Clear the breakfast table by putting the cup on the counter extending across the kitchen.",
        },
        "B_B": {
            "mb_bring_breakfast_cup": "Bring the cup from the kitchen counter to the breakfast table.",
            "mb_put_away_breakfast_cup": "Clear the breakfast table by putting the cup on the counter along the wall.",
        },
    },
    variants={"A_B": {}, "B_A": {}, "A_A": {}, "B_B": {}},
    object_usage={
        "mb_bring_breakfast_cup": {
            "used": ("countertop.n.01_1", "countertop.n.01_2", "breakfast_table.n.01_1", "coffee_cup.n.01_1"),
            "read": ("countertop.n.01_1", "countertop.n.01_2", "breakfast_table.n.01_1", "coffee_cup.n.01_1"),
            "write": ("coffee_cup.n.01_1",),
            "protected": ("countertop.n.01_1", "countertop.n.01_2", "breakfast_table.n.01_1", "coffee_cup.n.01_1"),
        },
        "mb_put_away_breakfast_cup": {
            "used": ("countertop.n.01_1", "countertop.n.01_2", "breakfast_table.n.01_1", "coffee_cup.n.01_1"),
            "read": ("countertop.n.01_1", "countertop.n.01_2", "breakfast_table.n.01_1", "coffee_cup.n.01_1"),
            "write": ("coffee_cup.n.01_1",),
            "protected": ("countertop.n.01_1", "countertop.n.01_2", "breakfast_table.n.01_1", "coffee_cup.n.01_1"),
        },
        "mb_retrieve_relocated_cup": {
            "used": ("countertop.n.01_1", "countertop.n.01_2", "breakfast_table.n.01_1", "coffee_cup.n.01_1"),
            "read": ("countertop.n.01_1", "countertop.n.01_2", "breakfast_table.n.01_1", "coffee_cup.n.01_1"),
            "write": ("coffee_cup.n.01_1",),
            "protected": ("countertop.n.01_1", "countertop.n.01_2", "breakfast_table.n.01_1", "coffee_cup.n.01_1"),
        },
    },
    scene_bindings={
        "countertop.n.01_1": "bar_byvbuc_0",
        "countertop.n.01_2": "bar_egwapq_0",
        "breakfast_table.n.01_1": "breakfast_table_xftrki_0",
    },
    top_surfaces=("countertop.n.01_1", "countertop.n.01_2", "breakfast_table.n.01_1"),
    initial_regions={
        "A_B": {"coffee_cup.n.01_1": ("countertop.n.01_1", 0.15, 0.2, 0.35, 0.8)},
        "B_A": {"coffee_cup.n.01_1": ("countertop.n.01_2", 0.2, 0.65, 0.8, 0.85)},
        "A_A": {"coffee_cup.n.01_1": ("countertop.n.01_1", 0.15, 0.2, 0.35, 0.8)},
        "B_B": {"coffee_cup.n.01_1": ("countertop.n.01_2", 0.2, 0.65, 0.8, 0.85)},
    },
    goal_regions={
        "A_B": {
            "key_1": {"coffee_cup.n.01_1": ("breakfast_table.n.01_1", 0.2, 0.3, 0.8, 0.7)},
            "key_2": {"coffee_cup.n.01_1": ("countertop.n.01_2", 0.2, 0.65, 0.8, 0.85)},
            "query": {"coffee_cup.n.01_1": ("breakfast_table.n.01_1", 0.2, 0.3, 0.8, 0.7)},
        },
        "B_A": {
            "key_1": {"coffee_cup.n.01_1": ("breakfast_table.n.01_1", 0.2, 0.3, 0.8, 0.7)},
            "key_2": {"coffee_cup.n.01_1": ("countertop.n.01_1", 0.15, 0.2, 0.35, 0.8)},
            "query": {"coffee_cup.n.01_1": ("breakfast_table.n.01_1", 0.2, 0.3, 0.8, 0.7)},
        },
        "A_A": {
            "key_1": {"coffee_cup.n.01_1": ("breakfast_table.n.01_1", 0.2, 0.3, 0.8, 0.7)},
            "key_2": {"coffee_cup.n.01_1": ("countertop.n.01_1", 0.15, 0.2, 0.35, 0.8)},
            "query": {"coffee_cup.n.01_1": ("breakfast_table.n.01_1", 0.2, 0.3, 0.8, 0.7)},
        },
        "B_B": {
            "key_1": {"coffee_cup.n.01_1": ("breakfast_table.n.01_1", 0.2, 0.3, 0.8, 0.7)},
            "key_2": {"coffee_cup.n.01_1": ("countertop.n.01_2", 0.2, 0.65, 0.8, 0.85)},
            "query": {"coffee_cup.n.01_1": ("breakfast_table.n.01_1", 0.2, 0.3, 0.8, 0.7)},
        },
    },
    variant_weights={"A_A": 38, "A_B": 37, "B_A": 37, "B_B": 38},
    init=VARIANT_INIT['A_B'],
    goal=VARIANT_GOAL['A_B'],
    variant_init=VARIANT_INIT,
    variant_goal=VARIANT_GOAL,
    initial_states={variant: conditions["key_1"] for variant, conditions in VARIANT_INIT.items()},
)
