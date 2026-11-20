#!/usr/bin/env bash
set -euo pipefail

# Run with the behavior environment already activated.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
OUTPUT_ROOT="$REPO/outputs/memory_bench_1116"
GPU_IDS=(6 7)
CONFIGS=(
    "$OUTPUT_ROOT/sessions/mb000_retrieve_bottle_of_milk/config.yaml"
    "$OUTPUT_ROOT/sessions/mb001_return_book_to_original_bookcase/config.yaml"
    "$OUTPUT_ROOT/sessions/mb002_place_reading_lamp_by_previous_state/config.yaml"
    "$OUTPUT_ROOT/sessions/mb003_place_radio_by_previous_state/config.yaml"
    "$OUTPUT_ROOT/sessions/mb004_put_away_mug_and_tissue_box/config.yaml"
    "$OUTPUT_ROOT/sessions/mb005_return_bowl_and_cutting_board/config.yaml"
    "$OUTPUT_ROOT/sessions/mb006_place_gaming_controller_on_original_nightstand/config.yaml"
    "$OUTPUT_ROOT/sessions/mb007_retrieve_relocated_meat_jar/config.yaml"
)

cd "$REPO"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONBREAKPOINT=0
export PYTHONUNBUFFERED=1
export OMNIGIBSON_HEADLESS=1

source "$SCRIPT_DIR/runtime_cache.sh"
init_parallel_runtime

worker() {
    local source="$1" task_dir="$2"
    local name="${source%/config.yaml}" config directory instance_number instance_id
    name="${name##*/}"
    local manifest selected_manifest demo_directory demo_number demo_id recipe_name code
    local failed=0 child_pid=""
    trap 'if [[ -n "$child_pid" ]]; then
        kill -TERM -- "-$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi; exit 130' INT TERM

    # Use the prepared session settings, with paths rooted in this checkout.
    config="$task_dir/config.yaml"
    sed \
        -e "s|^  output_root:.*|  output_root: $OUTPUT_ROOT|" \
        -e "s|^  omnigibson_data_root:.*|  omnigibson_data_root: $REPO/datasets|" \
        "$source" > "$config"
    shopt -s nullglob
    for directory in "$OUTPUT_ROOT/recordings/$name"/instance_*; do
        instance_number="${directory##*/instance_}"
        instance_id=$((10#$instance_number))
        printf -v recipe_name 'episode_%08d.json' "$((instance_id - 1))"
        if [[ -e "$OUTPUT_ROOT/recipes/$name/$recipe_name" ]]; then
            printf '[SKIP] instance %s: recipe already exists\n' "$instance_id"
            continue
        fi

        # Prefer the first complete collection round; ignore unfinished/aborted runs.
        selected_manifest=""
        for manifest in "$directory"/demo_*/manifest.json; do
            if rg -q '"status"[[:space:]]*:[[:space:]]*"complete"' "$manifest" \
                && ! rg -q '"invalid_reason"[[:space:]]*:[[:space:]]*"[^"]+"' "$manifest"; then
                selected_manifest="$manifest"
                break
            fi
        done
        if [[ -z "$selected_manifest" ]]; then
            printf '[SKIP] instance %s: no complete collection demo\n' "$instance_id"
            continue
        fi
        demo_directory="${selected_manifest%/manifest.json}"
        demo_number="${demo_directory##*/demo_}"
        demo_id=$((10#$demo_number))
        printf '[BUILD] instance %s, demo %s\n' "$instance_id" "$demo_id"

        # A fresh process per instance also lets later instances survive a simulator crash.
        setsid python -m memory_bench.episodes.build_episodes "$config" \
            --instance-id "$instance_id" --demo-id "$demo_id" \
            > "$task_dir/instance_$instance_number.log" 2>&1 &
        child_pid=$!
        if wait "$child_pid"; then
            printf '[OK] instance %s\n' "$instance_id"
        else
            code=$?
            printf '[FAILED] instance %s (exit=%s); log: %s/instance_%s.log\n' \
                "$instance_id" "$code" "$task_dir" "$instance_number"
            failed=1
        fi
        child_pid=""
    done
    return "$failed"
}

gpu_worker() {
    local slot="$1" gpu="${GPU_IDS[$1]}" index config name task_dir code
    local failed=0 task_pid=""
    trap 'if [[ -n "$task_pid" ]]; then
        kill -TERM "$task_pid" 2>/dev/null || true
        wait "$task_pid" 2>/dev/null || true
    fi; exit 130' INT TERM

    configure_parallel_worker
    # Rendering uses the physical GPU index; CUDA sees that GPU as device 0.
    export CUDA_DEVICE_ORDER=PCI_BUS_ID
    export CUDA_VISIBLE_DEVICES="$gpu"
    export OMNIGIBSON_GPU_ID="$gpu"
    export OMNIGIBSON_PHYSICS_GPU_ID=0

    # Each GPU processes its assigned tasks sequentially.
    for ((index = slot; index < ${#CONFIGS[@]}; index += ${#GPU_IDS[@]})); do
        config="${CONFIGS[index]}"
        name="${config%/config.yaml}"
        name="${name##*/}"
        task_dir="$REPO/.cache/$name"
        mkdir -p "$task_dir"
        printf '[START] GPU %s: %s; log: %s/build.log\n' "$gpu" "$name" "$task_dir"
        worker "$config" "$task_dir" > "$task_dir/build.log" 2>&1 &
        task_pid=$!
        if wait "$task_pid"; then
            printf '[OK] GPU %s: %s\n' "$gpu" "$name"
        else
            code=$?
            printf '[FAILED] GPU %s: %s (exit=%s); log: %s/build.log\n' \
                "$gpu" "$name" "$code" "$task_dir"
            failed=1
        fi
        task_pid=""
    done
    return "$failed"
}

pids=()
trap 'trap "" INT TERM; kill -TERM "${pids[@]}" 2>/dev/null || true; wait || true; exit 130' INT TERM
for slot in "${!GPU_IDS[@]}"; do
    gpu_worker "$slot" &
    pids+=("$!")
done

failed=0
for index in "${!pids[@]}"; do
    if wait "${pids[index]}"; then
        printf '[DONE] GPU %s: all assigned tasks succeeded\n' "${GPU_IDS[index]}"
    else
        printf '[FAILED] GPU %s: one or more assigned tasks failed\n' "${GPU_IDS[index]}"
        failed=1
    fi
done
exit "$failed"
