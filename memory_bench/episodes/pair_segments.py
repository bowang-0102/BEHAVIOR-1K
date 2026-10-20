"""Create one-to-one material groups with separately assigned task orders."""

import argparse
import random

from memory_bench.session import Session
from memory_bench.processing.process_recordings import validate_segment_index
from memory_bench.utils.scene_state import read_json, write_json


def match_sources(session, instances, kq_segments, intervening_segments, order_indices):
    """Use each valid source once; order allocation is supplied independently of memory cases."""
    count = session.instance_count
    if len(instances) != count or len(kq_segments) != count or len(order_indices) != count:
        raise ValueError("Pairing inputs must contain exactly the configured instance count")
    if any(len(paths) != count for paths in intervening_segments.values()):
        raise ValueError("Each I must provide one distinct source per instance")
    pairings = []
    for index, instance in enumerate(instances):
        order = session.composition["task_orders"][order_indices[index]]
        session.validate_order(order)
        pairings.append(
            {
                "episode_id": f"episode_{index:08d}",
                "instance": f"sessions/{session.session_id}/tro/instance_{instance['instance_id']:04d}.json",
                "task_order": order,
                "segments": {
                    **kq_segments[index],
                    **{key: paths[index] for key, paths in intervening_segments.items()},
                },
            }
        )
    return pairings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    orders = parser.add_mutually_exclusive_group(required=True)
    orders.add_argument("--order-indices", help="JSON array selecting the frozen order for each instance")
    orders.add_argument("--order-seed", type=int, help="Independent seed assigning one order to each material group")
    args = parser.parse_args()
    session = Session.load(args.config)
    root = session.paths.output_root / "sessions" / session.session_id
    instances = read_json(root / "case_assignments.json")["instances"]
    count = session.instance_count
    if args.order_indices:
        indices = read_json(session.paths.resolve(args.order_indices))
    else:
        if args.order_seed == session.instance_sampling["seed"]:
            raise ValueError("Use a separate order seed from the instance/case seed")
        rng = random.Random(args.order_seed)
        indices = [rng.randrange(len(session.composition["task_orders"])) for _ in range(count)]
    if any(
        not isinstance(index, int) or not 0 <= index < len(session.composition["task_orders"])
        for index in indices
    ):
        raise ValueError("Order indices must refer to frozen candidate task orders")
    index = validate_segment_index(session, read_json(root / "segment_index.json"))
    prepared = {item["instance_id"]: item for item in index["instances"]}
    kq_segments = [prepared[instance["instance_id"]]["segments"] for instance in instances]
    intervening = index["intervents"]
    pairings = match_sources(session, instances, kq_segments, intervening, indices)
    path = root / "material_groups.json"
    write_json(path, pairings)
    print(path)


if __name__ == "__main__":
    main()
