#!/usr/bin/env bash
set -euo pipefail

# 运行前先激活 behavior 环境。
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd -- "$SCRIPT_DIR/../../.." && pwd)

# 会话与输出
# 输出根目录：从 sessions/ 读取配置，向 recipes/ 和 reports/ 写入构建结果和报告。
OUTPUT_ROOT="$REPO/outputs/memory_bench_mb001_final"
# 会话 ID，对应 sessions/ 下包含 config.yaml 的目录名。
SESSION_ID="mb001_return_book_to_original_bookcase"

# 构建范围
# 起始实例编号（含），实例从 1 开始编号。
FIRST_INSTANCE=1
# 结束实例编号（含）。
LAST_INSTANCE=80

# GPU 与并发
# 参与构建的物理 GPU 编号，多张卡用空格分隔，例如 GPUS=(0 1)。
GPUS=(1)
# 每张卡最多同时运行的 worker 数，每个 worker 一次构建一个实例。
WORKERS_PER_GPU=4
# 相邻两个 worker 的启动间隔（秒），按 GPU 列表轮流启动。设为 0 可连续启动。
START_DELAY=30

SOURCE_CONFIG="$OUTPUT_ROOT/sessions/$SESSION_ID/config.yaml"
if [[ ! -f "$SOURCE_CONFIG" ]]; then
    printf 'Session config not found: %s\n' "$SOURCE_CONFIG" >&2
    exit 1
fi
if (( FIRST_INSTANCE < 1 || LAST_INSTANCE < FIRST_INSTANCE || WORKERS_PER_GPU < 1 || ${#GPUS[@]} == 0 )); then
    printf 'Set a valid instance range, worker count and GPU list.\n' >&2
    exit 2
fi

cd "$REPO"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONBREAKPOINT=0
export PYTHONUNBUFFERED=1
export OMNIGIBSON_HEADLESS=1

REPORT_DIR="$OUTPUT_ROOT/reports/$SESSION_ID"
mkdir -p -- "$REPORT_DIR"
exec {run_lock}<"$OUTPUT_ROOT"
if ! flock -n "$run_lock"; then
    printf 'Session already running: %s\n' "$OUTPUT_ROOT" >&2
    exit 1
fi
# 兼容仍在运行的旧版脚本使用的目录锁。
if [[ -d "$OUTPUT_ROOT/parallel" ]]; then
    exec {legacy_lock}<"$OUTPUT_ROOT/parallel"
    flock -n "$legacy_lock" || { printf 'Session already running: %s\n' "$OUTPUT_ROOT" >&2; exit 1; }
fi

BUILD_LOG="$REPORT_DIR/build_episodes_$(date +%Y%m%d_%H%M%S_%N).log"
printf 'Build log: %s\n' "$BUILD_LOG" | tee -- "$BUILD_LOG"

source "$SCRIPT_DIR/runtime_cache.sh"
init_parallel_runtime
pids=()
trap 'trap "" INT TERM; kill -TERM "${pids[@]}" 2>/dev/null || true; wait || true; exit 130' INT TERM

PREP_CONFIG="$RUN_STATE/config.yaml"
sed "s|^  output_root:.*|  output_root: $OUTPUT_ROOT|" "$SOURCE_CONFIG" > "$PREP_CONFIG"

printf '%s\n' "$FIRST_INSTANCE" > "$RUN_STATE/next_instance"

worker() {
    local gpu="$1" instance report_base child_pid="" worker_failed=0
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
        # 只在领取实例时加锁，构建可以并行。
        flock -x 9
        read -r instance < "$RUN_STATE/next_instance"
        if (( instance > LAST_INSTANCE )); then
            flock -u 9
            break
        fi
        printf '%s\n' "$((instance + 1))" > "$RUN_STATE/next_instance"
        flock -u 9

        printf -v report_base '%s/build_instance_%04d' "$REPORT_DIR" "$instance"
        printf '[START] build on GPU %s, instance %s\n' "$gpu" "$instance" | tee -a -- "$BUILD_LOG"
        setsid bash -o pipefail -c '
            instance=$1
            shift
            "$@" 2>&1 | sed -u "s/^/[instance $instance] /"
        ' build "$instance" python -m memory_bench.episodes.build_episodes "$PREP_CONFIG" \
            --instance-range "$instance-$instance" --report-path "$report_base.json" \
            >> "$BUILD_LOG" &
        child_pid=$!
        if wait "$child_pid"; then
            printf '[DONE] build on GPU %s, instance %s\n' "$gpu" "$instance" | tee -a -- "$BUILD_LOG"
        else
            printf '[FAILED] build on GPU %s, instance %s (exit=%s); log: %s\n' \
                "$gpu" "$instance" "$?" "$BUILD_LOG" | tee -a -- "$BUILD_LOG" >&2
            worker_failed=1
        fi
        child_pid=""
    done
    return "$worker_failed"
}

for ((slot = 0; slot < WORKERS_PER_GPU; slot++)); do
    for gpu in "${GPUS[@]}"; do
        if (( ${#pids[@]} )); then
            next_instance=$(flock -s "$RUN_STATE/queue.lock" cat "$RUN_STATE/next_instance")
            if (( next_instance > LAST_INSTANCE )); then
                break 2
            fi
            sleep "$START_DELAY"
        fi
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

# 已生成 recipe 的实例计为构建完成。
built=()
missing=()
unfinished=()
for ((id = FIRST_INSTANCE; id <= LAST_INSTANCE; id++)); do
    printf -v episode 'episode_%08d' "$((id - 1))"
    if [[ -f "$OUTPUT_ROOT/recipes/$SESSION_ID/$episode.json" ]]; then
        built+=("$id")
    elif grep -Fxq "[instance $id] [Build] instance $id: no complete collection demo, skipping" "$BUILD_LOG"; then
        missing+=("$id")
    else
        unfinished+=("$id")
        failed=1
    fi
done
{
    printf 'Built (%d): %s\n' "${#built[@]}" "${built[*]}"
    printf 'No complete demo (%d): %s\n' "${#missing[@]}" "${missing[*]}"
    printf 'Unfinished (%d): %s\n' "${#unfinished[@]}" "${unfinished[*]}"
} | tee -a -- "$BUILD_LOG"
exit "$failed"
