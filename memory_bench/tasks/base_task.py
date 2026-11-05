from dataclasses import dataclass, field
from functools import cached_property
import random
from typing import Any, Mapping


@dataclass(frozen=True)
class MemoryTask:
    # Task and scene.
    task_name: str
    scene_model: str
    room_types: tuple[str, ...]
    whitelist: dict[str, dict[str, dict[str, Any]]]

    # Activities and instructions.
    key_activities: tuple[str, ...]
    intervent_activities: tuple[str, ...]
    query_activity: str
    query_instruction: str
    key_instructions: dict[str, dict[str, str]] = field(default_factory=dict, kw_only=True)

    # Variant bindings.
    variants: dict[str, dict[str, str]]

    # Object access.
    object_usage: dict[str, dict[str, tuple[str, ...]]]

    # BDDL initial and goal conditions. Required fields must precede optional fields.
    goal: dict[str, list[list[Any]]]
    init: dict[str, list[list[Any]]] = field(default_factory=dict)
    variant_init: dict[str, dict[str, list[list[Any]]]] = field(default_factory=dict)
    variant_goal: dict[str, dict[str, list[list[Any]]]] = field(default_factory=dict)

    # Optional sampling constraints and variant bindings.
    activity_whitelists: dict[str, dict[str, dict[str, dict[str, Any]]]] = field(default_factory=dict)
    activity_variants: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    variant_weights: dict[str, float] = field(default_factory=dict)
    variant_object_models: dict[str, tuple[str, str]] = field(default_factory=dict)
    scene_bindings: dict[str, str] = field(default_factory=dict)
    shared_object_roles: tuple[str, ...] = ()
    # Flat predicates applied only while preparing an instance, never between Keys.
    initial_states: dict[str, list[list[Any]]] = field(default_factory=dict)
    top_surfaces: tuple[str, ...] = ()
    # Object -> (support role, xmin, ymin, xmax, ymax), fractions of the world XY AABB.
    initial_regions: dict[str, dict[str, tuple]] = field(default_factory=dict)
    goal_regions: dict[str, dict[str, dict[str, tuple]]] = field(default_factory=dict)

    def prepare_initial_state(self, objects, variant_id):
        """Materialize a variant before recording, retaining the sampled robot pose."""
        if variant_id not in self.initial_states:
            return
        import omnigibson as og
        from omnigibson.object_states import Inside, OnTop, Open

        conditions = self.initial_states[variant_id]
        regions = self.initial_regions.get(variant_id, {})
        # Open containers for placement; set the requested final door states last.
        containers = {condition[2] for condition in conditions if condition[0] == "inside"}
        for name in containers:
            if Open in objects[name].states and not objects[name].states[Open].set_value(True, fully=True):
                raise ValueError(f"Cannot open {name} for initial placement")
        for condition in conditions:
            predicate, *names = condition
            if predicate not in {"ontop", "inside"}:
                continue
            obj, support = (objects[name] for name in names)
            state = OnTop if predicate == "ontop" else Inside
            for _ in range(30):
                if obj.states[state].set_value(support, True) and self._check_regions([condition], objects, regions):
                    break
            else:
                raise ValueError(f"Cannot place {names[0]} for variant {variant_id}: {condition}")
        for condition in conditions:
            value = condition[0] != "not"
            atom = condition if value else condition[1]
            if atom[0] == "open" and not objects[atom[1]].states[Open].set_value(value, fully=True):
                raise ValueError(f"Cannot set initial open state: {condition}")
        for _ in range(30):
            og.sim.step_physics()

    def _check_regions(self, conditions, objects, regions):
        for condition in conditions:
            if condition[0] == "ontop" and condition[2] in self.top_surfaces:
                obj, support = objects[condition[1]], objects[condition[2]]
                if float(obj.aabb[0][2]) < float(support.aabb[1][2]) - 0.03:
                    return False
        for name, (support_name, xmin, ymin, xmax, ymax) in regions.items():
            # Check only the object currently being placed during initialization.
            if not any(c[0] in {"ontop", "inside"} and c[1] == name for c in conditions):
                continue
            low, high = objects[support_name].aabb
            # A lid changes the AABB center without moving the container's base.
            position, _ = objects[name].get_position_orientation()
            relative = (position[:2] - low[:2]) / (high[:2] - low[:2])
            if not (xmin <= float(relative[0]) <= xmax and ymin <= float(relative[1]) <= ymax):
                return False
        return True

    def instruction_for(self, activity: str, variant_id: str | None = None) -> str:
        """Return the instruction frozen for an activity and sampled variant."""
        if variant_id is not None and variant_id not in self.variants:
            raise KeyError(f"Unknown variant {variant_id!r} for {self.task_name}")
        if activity == self.query_activity:
            return self.query_instruction
        try:
            return self.key_instructions[variant_id][activity]
        except KeyError as error:
            raise KeyError(
                f"No Key instruction declared for {activity!r} in variant {variant_id!r} of {self.task_name}"
            ) from error

    def whitelist_for(self, activity: str) -> dict[str, dict[str, dict[str, Any]]]:
        """Return activity-specific sampling constraints, falling back to the task-wide set."""
        return self.activity_whitelists.get(activity, self.whitelist)

    def bind_variant(
        self,
        inst_to_name: Mapping[str, str],
        variant_id: str,
        activity: str | None = None,
        variant_objects: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Apply frozen bindings while keeping every sampled scene object assigned once."""
        bindings = dict(inst_to_name)
        if variant_id not in self.variants:
            raise KeyError(f"Unknown variant {variant_id!r} for {self.task_name}")
        if activity is not None and activity in self.activity_variants.get(variant_id, {}):
            overrides = self.activity_variants[variant_id][activity]
        else:
            overrides = self.variants[variant_id]
        if variant_objects is not None:
            overrides = {instance: variant_objects.get(name, name) for instance, name in overrides.items()}
        missing_instances = set(overrides) - set(bindings)
        if missing_instances:
            raise ValueError(f"{activity or self.task_name} is missing BDDL instances: {sorted(missing_instances)}")

        original_bindings = dict(bindings)
        for instance, object_name in overrides.items():
            if object_name not in original_bindings.values():
                raise ValueError(f"Variant object {object_name!r} is absent from the sampled bindings")
            holder = next(name for name, bound_object in original_bindings.items() if bound_object == object_name)
            if holder not in overrides:
                bindings[holder] = original_bindings[instance]
        bindings.update(overrides)
        if len(set(bindings.values())) != len(bindings):
            raise ValueError("Variant bindings must map BDDL instances to distinct scene objects")
        return bindings

    def sample_variants(self, count: int, seed: int) -> dict[int, str]:
        """Create a balanced, shuffled variant assignment and freeze it by instance ID."""
        names = sorted(self.variants)
        weights = {name: self.variant_weights.get(name, 1.0) for name in names}
        total_weight = sum(weights.values())
        if not names or any(weight <= 0 for weight in weights.values()) or total_weight <= 0:
            raise ValueError(f"Variant weights must be positive for every variant in {self.task_name}")
        exact = {name: count * weights[name] / total_weight for name in names}
        counts = {name: int(exact[name]) for name in names}
        remainder = count - sum(counts.values())
        for name in sorted(names, key=lambda item: exact[item] - counts[item], reverse=True)[:remainder]:
            counts[name] += 1

        assignments = [name for name in names for _ in range(counts[name])]
        random.Random(seed).shuffle(assignments)
        return dict(enumerate(assignments, start=1))

    @cached_property
    def _compiled_conditions(self):
        # Runtime expressions are excluded from dataclass fields and saved task definitions.
        return {}

    def _compile_conditions(self, phase, segment_id, objects, variant_id):
        from bddl.condition_evaluation import compile_state

        names = tuple(sorted(objects))
        key = (phase, segment_id, variant_id, names)
        if key not in self._compiled_conditions:
            defaults = self.init if phase == "init" else self.goal
            overrides = self.variant_init if phase == "init" else self.variant_goal
            conditions = defaults[segment_id]
            conditions = overrides.get(variant_id, {}).get(segment_id, conditions)
            object_map = {}
            for name in names:
                synset = name.rsplit("_", 1)[0]
                object_map.setdefault(synset, []).append(name)
            self._compiled_conditions[key] = compile_state(
                conditions, scope=set(names), object_map=object_map, generate_ground_options=False
            )
        return self._compiled_conditions[key]

    def prepare_conditions(self, bindings, variant_id: str | None = None):
        """Compile each segment once from logical names, without retaining scene objects."""
        for segment_id, objects in bindings.items():
            self._compile_conditions("init", segment_id, objects, variant_id)
            self._compile_conditions("goal", segment_id, objects, variant_id)

    def _evaluate_conditions(self, compiled, objects: Mapping[str, Any]) -> bool:
        from bddl.condition_evaluation import evaluate_state
        from omnigibson.utils.bddl_utils import evaluate_bddl_predicate

        def evaluate(predicate, *names):
            return evaluate_bddl_predicate(predicate, *(objects[name] for name in names))

        satisfied, _ = evaluate_state(compiled, evaluate)
        return satisfied

    def check_init(self, segment_id: str, objects: Mapping[str, Any], variant_id: str | None = None) -> bool:
        """Check the BDDL initial conditions at a Key or Query entry boundary."""
        compiled = self._compile_conditions("init", segment_id, objects, variant_id)
        conditions = self.variant_init.get(variant_id, {}).get(segment_id, self.init[segment_id])
        regions = self.initial_regions.get(variant_id, {}) if segment_id == "key_1" else {}
        return self._evaluate_conditions(compiled, objects) and self._check_regions(conditions, objects, regions)

    def check_goal(self, segment_id: str, objects: Mapping[str, Any], variant_id: str | None = None) -> bool:
        """Check the segment's BDDL goal conditions against live objects."""
        compiled = self._compile_conditions("goal", segment_id, objects, variant_id)
        conditions = self.variant_goal.get(variant_id, {}).get(segment_id, self.goal[segment_id])
        regions = self.goal_regions.get(variant_id, {}).get(segment_id, {})
        return self._evaluate_conditions(compiled, objects) and self._check_regions(conditions, objects, regions)
