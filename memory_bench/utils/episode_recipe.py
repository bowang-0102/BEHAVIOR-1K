from copy import deepcopy

from memory_bench.utils.recordings import recording_metadata
from memory_bench.utils.segment_manifest import load_segments


def recipe_segment(segment):
    """Keep only the source, boundaries and object scopes needed for composition."""
    result = {
        "id": segment["identity"]["id"],
        **{key: segment["task"][key] for key in ("role", "activity", "instruction")},
        "source": {key: deepcopy(segment["source"][key]) for key in ("hdf5", "demo", "frames")},
    }
    if "states" in segment:
        result["states"] = {key: segment["states"][key] for key in ("directory", "entry", "exit")}
    result["objects"] = {key: deepcopy(segment["objects"][key]) for key in ("used", "read", "write")}
    return result


def load_recipe_segments(session, recipe):
    """Read interfaces from the actual sources and restore internal segment descriptions."""
    segments = {}
    for segment in recipe["segments"]:
        key = segment["id"]
        if key in segments:
            raise ValueError(f"Duplicate recipe segment: {key}")
        source = segment["source"]
        metadata = recording_metadata(session.paths.resolve(source["hdf5"]), source["demo"])
        item = {
            "identity": {"id": key},
            "task": {name: segment[name] for name in ("role", "activity", "instruction")},
            "source": deepcopy(source),
            "objects": deepcopy(segment["objects"]),
            "interface": metadata["interface"],
        }
        if "states" in segment:
            item["states"] = deepcopy(segment["states"])
        segments[key] = item
    return load_segments(segments)


def episode_annotations(recipe, episode_index):
    """Build self-contained training labels without source paths or composition settings."""
    segments = [
        {key: deepcopy(segment[key]) for key in ("id", "role", "activity", "instruction", "frames")}
        for segment in recipe["segments"]
    ]
    queries = [segment for segment in segments if segment["role"] == "query_task"]
    if len(queries) != 1:
        raise ValueError("An exported episode must contain exactly one Query")
    query = queries[0]
    return {
        **{key: recipe[key] for key in ("session_id", "episode_id", "instance_id")},
        "episode_index": episode_index,
        **{key: recipe["output"][key] for key in ("fps", "total_frames")},
        "segments": segments,
        "memory_spans": [
            {
                "key_id": segment["id"], "query_id": query["id"],
                "key_end_frame": segment["frames"][1], "query_start_frame": query["frames"][0],
            }
            for segment in segments if segment["role"] == "key_task"
        ],
    }


def validate_annotations(annotation):
    """Check contiguous action ranges and memory spans before rendering or reading frames."""
    cursor = 0
    by_id = {}
    for segment in annotation["segments"]:
        start, end = segment["frames"]
        if start != cursor or end <= start or segment["id"] in by_id:
            raise ValueError("Annotations must cover every frame once, contiguously")
        cursor = end
        by_id[segment["id"]] = segment
    if cursor != annotation["total_frames"]:
        raise ValueError("Annotation length does not match episode")
    for span in annotation["memory_spans"]:
        key = by_id[span["key_id"]]
        query = by_id[span["query_id"]]
        if (
            key["role"] != "key_task" or query["role"] != "query_task"
            or span["key_end_frame"] != key["frames"][1]
            or span["query_start_frame"] != query["frames"][0]
            or not 0 < span["key_end_frame"] <= span["query_start_frame"] < cursor
        ):
            raise ValueError("Invalid memory span")
