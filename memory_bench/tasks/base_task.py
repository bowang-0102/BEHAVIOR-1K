from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryTask:
    task_name: str
    scene_model: str
    key_activity: list[str] # 执行query task依赖的任务可以是多个
    distractor_activity: list[str] # 干扰任务可以是多个
    query_activity: str # 最终要做的任务一个
    query_instruction: str
    room_types: list[str]
    # Sampling whitelist in task_custom_lists.json format: synset -> category -> model -> bbox or None.
    whitelist: dict
    # Variant name -> BDDL instance -> scene object that instance must be bound to.
    variants: dict[str, dict[str, str]]

    def bind_variant(self, inst_to_name: dict[str, str], variant: str) -> dict[str, str]:
        """Swap sampled bindings so the variant's instances point at its scene objects."""
        bindings = dict(inst_to_name)
        for inst, name in self.variants[variant].items():
            holder = {bound_name: bound_inst for bound_inst, bound_name in bindings.items()}[name]
            bindings[inst], bindings[holder] = name, bindings[inst]
        return bindings
