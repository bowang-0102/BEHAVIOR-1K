"""Declarative task metadata and live checks for Memory Bench sessions."""

from dataclasses import dataclass, field
import random
from typing import Any, Mapping


@dataclass(frozen=True)
class MemoryTask:
    task_name: str
    scene_model: str
    key_activities: tuple[str, ...]
    intervent_activities: tuple[str, ...]
    query_activity: str
    query_instruction: str
    room_types: tuple[str, ...]
    whitelist: dict[str, dict[str, dict[str, Any]]]
    variants: dict[str, dict[str, str]]
    segment_instructions: dict[str, str]
    key_information: dict[str, dict[str, Any]]
    key_dependencies: dict[str, tuple[str, ...]]
    object_usage: dict[str, dict[str, tuple[str, ...]]]
    segment_checks: dict[str, tuple[tuple[str, tuple[str, ...]], ...]]
    query_cabinets: tuple[str, ...]
    query_target_cabinets: dict[str, tuple[str, ...]]
    activity_variants: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    case_weights: dict[str, float] = field(default_factory=dict)
    key_instructions: dict[str, dict[str, str]] = field(default_factory=dict)
    activity_whitelists: dict[str, dict[str, dict[str, dict[str, Any]]]] = field(default_factory=dict)
    case_segment_checks: dict[str, dict[str, tuple[tuple[str, tuple[str, ...]], ...]]] = field(default_factory=dict)
    entry_checks: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = field(default_factory=dict)
    case_entry_checks: dict[str, dict[str, tuple[tuple[str, tuple[str, ...]], ...]]] = field(default_factory=dict)
    variant_object_models: dict[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def key_activity(self) -> list[str]:
        """Compatibility alias for callers that displayed the old single-Key field."""
        return list(self.key_activities)

    @property
    def intervent_activity(self) -> list[str]:
        return list(self.intervent_activities)

    def binding_overrides(self, case_id: str, activity: str | None = None) -> dict[str, str]:
        """Return BDDL-instance to scene-object overrides for one case and activity."""
        if case_id not in self.variants:
            raise KeyError(f"Unknown case {case_id!r} for {self.task_name}")
        if activity is not None and activity in self.activity_variants.get(case_id, {}):
            return self.activity_variants[case_id][activity]
        return self.variants[case_id]

    def instruction_for(self, activity: str, case_id: str | None = None) -> str:
        """Return the instruction frozen for an activity and sampled case."""
        if case_id is not None and case_id not in self.variants:
            raise KeyError(f"Unknown case {case_id!r} for {self.task_name}")
        if activity == self.query_activity:
            return self.query_instruction
        if case_id is not None and activity in self.key_instructions.get(case_id, {}):
            return self.key_instructions[case_id][activity]
        try:
            return self.segment_instructions[activity]
        except KeyError as error:
            raise KeyError(f"No instruction declared for {activity!r} in {self.task_name}") from error

    def whitelist_for(self, activity: str) -> dict[str, dict[str, dict[str, Any]]]:
        """Return activity-specific sampling constraints, falling back to the task-wide set."""
        return self.activity_whitelists.get(activity, self.whitelist)

    def bind_variant(
        self,
        inst_to_name: Mapping[str, str],
        variant: str,
        activity: str | None = None,
        variant_objects: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Apply frozen bindings while keeping every sampled scene object assigned once."""
        bindings = dict(inst_to_name)
        overrides = self.binding_overrides(variant, activity)
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

    def sample_cases(self, count: int, seed: int) -> dict[int, str]:
        """Create a balanced, shuffled case assignment and freeze it by instance ID."""
        names = sorted(self.variants)
        if count <= 0:
            raise ValueError("Instance count must be positive")
        weights = {name: self.case_weights.get(name, 1.0) for name in names}
        total_weight = sum(weights.values())
        if not names or any(weight <= 0 for weight in weights.values()) or total_weight <= 0:
            raise ValueError(f"Case weights must be positive for every case in {self.task_name}")
        exact = {name: count * weights[name] / total_weight for name in names}
        counts = {name: int(exact[name]) for name in names}
        remainder = count - sum(counts.values())
        for name in sorted(names, key=lambda item: exact[item] - counts[item], reverse=True)[:remainder]:
            counts[name] += 1

        assignments = [name for name in names for _ in range(counts[name])]
        random.Random(seed).shuffle(assignments)
        return dict(enumerate(assignments, start=1))

    def _evaluate_checks(self, checks, objects: Mapping[str, Any]) -> bool:
        from omnigibson.object_states import Inside, OnTop, Open

        for predicate, instances in checks:
            if predicate == "inside":
                satisfied = objects[instances[0]].states[Inside].get_value(objects[instances[1]])
            elif predicate == "ontop":
                satisfied = objects[instances[0]].states[OnTop].get_value(objects[instances[1]])
            elif predicate == "closed":
                satisfied = not objects[instances[0]].states[Open].get_value()
            elif predicate == "all_closed":
                satisfied = all(not objects[instance].states[Open].get_value() for instance in instances)
            else:
                raise ValueError(f"Unknown live check {predicate!r} in {self.task_name}")
            if not satisfied:
                return False
        return True

    def check_entry(self, segment_id: str, objects: Mapping[str, Any], case_id: str | None = None) -> bool:
        """Check the BDDL preconditions at a Key or Query entry boundary."""
        checks = self.entry_checks.get(segment_id)
        if checks is None:
            raise KeyError(f"No live entry preconditions declared for {segment_id!r} in {self.task_name}")
        if case_id is not None:
            checks = self.case_entry_checks.get(case_id, {}).get(segment_id, checks)
        return self._evaluate_checks(checks, objects)

    def check_segment(self, segment_id: str, role: str, objects: Mapping[str, Any], case_id: str | None = None) -> bool:
        """Check the segment's BDDL postcondition against live objects."""
        del role
        checks = self.segment_checks[segment_id]
        if case_id is not None:
            checks = self.case_segment_checks.get(case_id, {}).get(segment_id, checks)
        return self._evaluate_checks(checks, objects)

    def query_events(
        self,
        objects: Mapping[str, Any],
        previous_open: Mapping[str, bool] | None = None,
        case_id: str | None = None,
    ) -> tuple[list[dict[str, str]], dict[str, bool]]:
        """Report newly opened non-target cabinets while Query is active."""
        from omnigibson.object_states import Open

        previous_open = {} if previous_open is None else previous_open
        if case_id not in self.query_target_cabinets:
            raise ValueError(f"Unknown query case {case_id!r} for {self.task_name}")
        target_cabinets = set(self.query_target_cabinets[case_id])
        current_open = {instance: objects[instance].states[Open].get_value() for instance in self.query_cabinets}
        events = [
            {
                "type": "wrong_cabinet_opened",
                "bddl_instance": instance,
                "object_id": objects[instance].name,
            }
            for instance, is_open in current_open.items()
            if is_open and not previous_open.get(instance, False) and instance not in target_cabinets
        ]
        return events, current_open
