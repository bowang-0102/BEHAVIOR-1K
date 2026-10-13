"""Create one-to-one material groups with separately assigned task orders."""

import argparse
import random

from memory_bench.processing.prepare_intervents import match_sources
from memory_bench.session import Session
from memory_bench.utils.state import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    orders = parser.add_mutually_exclusive_group(required=True)
    orders.add_argument("--order-indices", help="JSON array selecting the frozen order for each instance")
    orders.add_argument("--order-seed", type=int, help="Independent seed assigning one order to each material group")
    args = parser.parse_args()
    session = Session.load(args.config)
    root = session.paths.output_root / "sessions" / session.session_id
    instances = read_json(root / "instances.json")["instances"]
    count = session.config["instance_sampling"]["count"]
    if args.order_indices:
        indices = read_json(session.paths.resolve(args.order_indices))
    else:
        if args.order_seed == session.config["instance_sampling"]["seed"]:
            raise ValueError("Use a separate order seed from the instance/case seed")
        rng = random.Random(args.order_seed)
        indices = [rng.randrange(len(session.config["composition"]["task_orders"])) for _ in range(count)]
    if any(
        not isinstance(index, int) or not 0 <= index < len(session.config["composition"]["task_orders"])
        for index in indices
    ):
        raise ValueError("Order indices must refer to frozen candidate task orders")
    joint = []
    for instance in instances:
        directory = (
            session.paths.output_root / "segments" / session.session_id / f"instance_{instance['instance_id']:04d}"
        )
        joint.append(
            {key: session.paths.relative(directory / f"{key}.json") for key in [*session.key_ids, session.query_id]}
        )
    intervening = read_json(root / "intervent_segments.json")
    pairings = match_sources(session, instances, joint, intervening, indices)
    path = root / "material_groups.json"
    write_json(path, pairings)
    print(path)


if __name__ == "__main__":
    main()
