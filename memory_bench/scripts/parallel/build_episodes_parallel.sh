#!/usr/bin/env bash
set -euo pipefail

# 运行前先激活 behavior 环境。
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd -- "$SCRIPT_DIR/../../.." && pwd)
OUTPUT_ROOT="/data/bwang/datasets/memory_bench_mb001_final"
SESSION_ID="mb001_return_book_to_original_bookcase"
FIRST_INSTANCE=1
LAST_INSTANCE=80
GPUS=(0 4)
# 每张卡每次领取的实例数；每张卡一次只跑一个阶段。
BATCH_SIZE=4
# 两张卡错开启动，避免同时加载仿真环境。单位为秒。
START_DELAY=30

SOURCE_CONFIG="$OUTPUT_ROOT/sessions/$SESSION_ID/config.yaml"
if [[ ! -f "$SOURCE_CONFIG" ]]; then
    printf 'Session config not found: %s\n' "$SOURCE_CONFIG" >&2
    exit 1
fi
if (( FIRST_INSTANCE < 1 || LAST_INSTANCE < FIRST_INSTANCE || BATCH_SIZE < 1 || ${#GPUS[@]} == 0 )); then
    printf 'Set a valid instance range, batch size and GPU list.\n' >&2
    exit 2
fi

cd "$REPO"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONBREAKPOINT=0
export PYTHONUNBUFFERED=1
export OMNIGIBSON_HEADLESS=1

PARALLEL_DIR="$OUTPUT_ROOT/parallel"
mkdir -p -- "$PARALLEL_DIR"
exec {run_lock}<"$PARALLEL_DIR"
if ! flock -n "$run_lock"; then
    printf 'Session already running: %s\n' "$OUTPUT_ROOT" >&2
    exit 1
fi

source "$SCRIPT_DIR/runtime_cache.sh"
init_parallel_runtime
pids=()
trap 'trap "" INT TERM; kill -TERM "${pids[@]}" 2>/dev/null || true; wait || true; exit 130' INT TERM

run_stage() {
    local stage="$1" gpu="$2" range_root="$3" first="$4" last="$5"
    local config="$PREP_CONFIG" log="$range_root/$stage.log"
    local status instance export_dir="" failed_export failed=0
    local options=()

    if [[ "$stage" == replay ]]; then
        config="$range_root/config.yaml"
        options=(--rgb)
        export_dir="$range_root/lerobot_shards/$SESSION_ID"
    fi

    : > "$log"
    printf '[START] %s on GPU %s, instances %s-%s; log: %s\n' "$stage" "$gpu" "$first" "$last" "$log"
    while (( first <= last )); do
        printf '[RUN] %s-%s\n' "$first" "$last" >> "$log"
        if [[ "$stage" == build ]]; then
            options=(--report-path "$range_root/build_$first-$last.json")
        fi
        setsid python -m "memory_bench.episodes.${stage}_episodes" "$config" \
            --instance-range "$first-$last" "${options[@]}" >> "$log" 2>&1 &
        child_pid=$!
        if wait "$child_pid"; then
            child_pid=""
            break
        else
            status=$?
        fi
        child_pid=""
        # 从本次 RUN 的日志找出已开始但未结束的实例，跳过它，从下一个实例继续。
        instance=$(awk '
            /^\[(RUN|END)\] / { active = "" }
            /^\[START\] instance [0-9]+$/ { active = $3 }
            END { print active }
        ' "$log")
        if [[ "$stage" == replay && -z "$instance" && "$status" == 65 && -d "$export_dir" ]]; then
            # 回放启动检查返回 EX_DATAERR。保留损坏分片，用完整区间重建，跳过本轮失败实例。
            failed_export=$(mktemp -d "$range_root/failed_replay.XXXXXX")
            mv -- "$export_dir" "$failed_export/"
            printf 'Moved incomplete shard to %s; rebuilding instances %s-%s\n' \
                "$failed_export" "$first" "$last" | tee -a "$log" >&2
            continue
        fi
        failed=1
        if [[ -z "$instance" ]]; then
            printf '[FAILED] %s on GPU %s (exit=%s); log: %s\n' "$stage" "$gpu" "$status" "$log" >&2
            break
        fi
        printf '[FAILED] %s on GPU %s, instance %s (exit=%s); continuing with next instance\n' \
            "$stage" "$gpu" "$instance" "$status" | tee -a "$log" >&2

        if [[ "$stage" == replay ]]; then
            options+=(--skip-instance "$instance")
        else
            first=$((instance + 1))
        fi
    done

    if (( failed )); then
        worker_failed=1
    else
        printf '[DONE] %s on GPU %s; log: %s\n' "$stage" "$gpu" "$log"
    fi
}

PREP_CONFIG="$RUN_STATE/config.yaml"
sed "s|^  output_root:.*|  output_root: $OUTPUT_ROOT|" "$SOURCE_CONFIG" > "$PREP_CONFIG"

printf '%s\n' "$FIRST_INSTANCE" > "$RUN_STATE/next_instance"

worker() {
    local gpu="$1" first last range_root directory stage child_pid="" worker_failed=0
    trap 'if [[ -n "$child_pid" ]]; then
        kill -TERM -- "-$child_pid" 2>/dev/null || true
        wait "$child_pid" || true
    fi; exit 130' INT TERM

    configure_parallel_worker
    # Kit 使用物理卡号；对当前进程，CUDA 将这张卡编号为 0。
    export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES="$gpu"
    export OMNIGIBSON_GPU_ID="$gpu" OMNIGIBSON_PHYSICS_GPU_ID=0

    exec 9>"$RUN_STATE/queue.lock"
    while true; do
        # 只在领取实例区间时加锁，避免两张卡领到同一批。
        flock -x 9
        read -r first < "$RUN_STATE/next_instance"
        if (( first > LAST_INSTANCE )); then
            flock -u 9
            break
        fi
        last=$((first + BATCH_SIZE - 1))
        if (( last > LAST_INSTANCE )); then
            last=$LAST_INSTANCE
        fi
        printf '%s\n' "$((last + 1))" > "$RUN_STATE/next_instance"
        flock -u 9

        # 回放读取共享的轨迹文件，每批的 LeRobot 数据单独保存。
        range_root="$PARALLEL_DIR/$first-$last"
        mkdir -p -- "$range_root"
        for directory in sessions recipes episodes; do
            if [[ "$(readlink -- "$range_root/$directory")" != "$OUTPUT_ROOT/$directory" ]]; then
                ln -sT -- "$OUTPUT_ROOT/$directory" "$range_root/$directory"
            fi
        done
        sed "s|^  output_root:.*|  output_root: $range_root|" "$SOURCE_CONFIG" > "$range_root/config.yaml"
        # 同一张卡依次完成本批的构建、拼接和回放，再领取下一批。
        for stage in build compose replay; do
            run_stage "$stage" "$gpu" "$range_root" "$first" "$last"
        done
    done
    return "$worker_failed"
}

for gpu in "${GPUS[@]}"; do
    if (( ${#pids[@]} )); then
        sleep "$START_DELAY"
    fi
    worker "$gpu" &
    pids+=("$!")
done

failed=0
for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
        failed=1
    fi
done

# 回放写出标注才算导出成功；缺少完整录制的实例由 build 日志识别。
exported=()
missing=()
unfinished=()
for ((id = FIRST_INSTANCE; id <= LAST_INSTANCE; id++)); do
    first=$((id - (id - FIRST_INSTANCE) % BATCH_SIZE))
    last=$((first + BATCH_SIZE - 1))
    if (( last > LAST_INSTANCE )); then
        last=$LAST_INSTANCE
    fi
    range_root="$PARALLEL_DIR/$first-$last"
    printf -v episode 'episode_%08d' "$((id - 1))"
    if [[ -f "$range_root/lerobot_shards/$SESSION_ID/annotations/$episode.json" ]]; then
        exported+=("$id")
    elif [[ -f "$range_root/build.log" ]] && grep -Fxq "[Build] instance $id: no complete collection demo, skipping" "$range_root/build.log"; then
        missing+=("$id")
    else
        unfinished+=("$id")
        failed=1
    fi
done
printf 'Exported (%d): %s\n' "${#exported[@]}" "${exported[*]}"
printf 'No complete demo (%d): %s\n' "${#missing[@]}" "${missing[*]}"
printf 'Unfinished (%d): %s\n' "${#unfinished[@]}" "${unfinished[*]}"
exit "$failed"
