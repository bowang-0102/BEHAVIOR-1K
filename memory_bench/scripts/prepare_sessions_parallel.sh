#!/usr/bin/env bash
set -euo pipefail

# Run with the behavior environment already activated.
REPO=/data/bwang/code/BEHAVIOR-1K
GPU_IDS=(0 1 2)
WORKERS_PER_GPU=2
CONFIGS=("$REPO"/memory_bench/configs/mb*.yaml)
OUTPUT_ROOT="$REPO/outputs/memory_bench_1112"

cd "$REPO"
export PYTHONPATH="$PWD"
export PYTHONBREAKPOINT=0
export OMNIGIBSON_HEADLESS=1
RUN_STATE=$(mktemp -d)
trap 'rm -rf -- "$RUN_STATE"' EXIT
printf '0\n' > "$RUN_STATE/next_job"

# Initialize sequentially because tasks share sampler metadata and stable scenes.
for source in "${CONFIGS[@]}"; do
    name="${source##*/}"
    session_root="$OUTPUT_ROOT/sessions/${name%.yaml}"
    report_root="$OUTPUT_ROOT/reports/${name%.yaml}"
    if [[ -e "$session_root/session.json" ]]; then
        printf 'Session already prepared: %s\n' "$session_root" >&2
        exit 1
    fi
    mkdir -p "$session_root" "$report_root"
    sed \
        -e "s|^  output_root:.*|  output_root: $OUTPUT_ROOT|" \
        -e '/^instance_sampling:/,/^[^[:space:]]/{
            s/^  count:.*/  count: 5/
            s/^  seed:.*/  seed: 42/
        }' \
        "$source" > "$session_root/config.yaml"
    python -m memory_bench.collection.prepare_session "$session_root/config.yaml" init \
        > "$report_root/prepare.log" 2>&1
done

worker() {
    local gpu="$1" index name config report_root code
    local failed=0 child_pid=""
    # Each worker opens the queue lock independently.
    exec 9> "$RUN_STATE/queue.lock"
    trap 'if [[ -n "$child_pid" ]]; then
        kill -TERM -- "-$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi; exit 130' INT TERM

    while true; do
        flock -x 9
        read -r index < "$RUN_STATE/next_job"
        if (( index >= ${#CONFIGS[@]} )); then
            flock -u 9
            break
        fi
        printf '%s\n' "$((index + 1))" > "$RUN_STATE/next_job"
        flock -u 9

        name="${CONFIGS[index]##*/}"
        config="$OUTPUT_ROOT/sessions/${name%.yaml}/config.yaml"
        report_root="$OUTPUT_ROOT/reports/${name%.yaml}"
        printf '[START] GPU %s: %s\n' "$gpu" "${name%.yaml}"

        # Rendering uses the physical GPU index; CUDA sees that GPU as device 0.
        MB_CONFIG="$config" \
        CUDA_DEVICE_ORDER=PCI_BUS_ID \
        CUDA_VISIBLE_DEVICES="$gpu" \
        OMNIGIBSON_GPU_ID="$gpu" \
        OMNIGIBSON_PHYSICS_GPU_ID=0 \
        OMNIGIBSON_APPDATA_PATH="$RUN_STATE/${name%.yaml}/appdata" \
        OMNIGIBSON_HEADLESS=1 \
        PYTHONUNBUFFERED=1 \
            setsid bash -e -c '
                python -m memory_bench.collection.prepare_session "$MB_CONFIG" sample
                python -m memory_bench.collection.prepare_session "$MB_CONFIG" assign-variants
                python -m memory_bench.collection.prepare_intervent "$MB_CONFIG"
                python -m memory_bench.collection.sample_instances "$MB_CONFIG"
            ' >> "$report_root/prepare.log" 2>&1 &
        child_pid=$!
        if wait "$child_pid"; then
            printf '[OK] GPU %s: %s\n' "$gpu" "${name%.yaml}"
        else
            code=$?
            printf '[FAILED] GPU %s: %s (exit=%s), log: %s\n' \
                "$gpu" "${name%.yaml}" "$code" "$report_root/prepare.log"
            failed=1
        fi
        child_pid=""
    done
    return "$failed"
}

pids=()
trap 'trap "" INT TERM; kill -TERM "${pids[@]}" 2>/dev/null || true; wait || true; exit 130' INT TERM
printf 'Output directory: %s\n' "$OUTPUT_ROOT"
for gpu in "${GPU_IDS[@]}"; do
    for ((slot = 0; slot < WORKERS_PER_GPU; slot++)); do
        worker "$gpu" &
        pids+=("$!")
    done
done

failed=0
for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
        failed=1
    fi
done
printf 'Finished. Logs: %s/reports/*/prepare.log\n' "$OUTPUT_ROOT"
exit "$failed"
