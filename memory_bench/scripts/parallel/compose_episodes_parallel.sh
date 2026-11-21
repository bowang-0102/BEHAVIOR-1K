#!/usr/bin/env bash
set -euo pipefail

# 运行前先激活 behavior 环境。
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd -- "$SCRIPT_DIR/../../.." && pwd)

# 会话与输出
# 输出根目录：读取 sessions/ 和 recipes/，写入 episodes/ 并更新对应 recipe。
OUTPUT_ROOT=${OUTPUT_ROOT:-"$REPO/outputs/memory_bench_mb001_final"}
# 会话 ID，对应 sessions/ 下包含 config.yaml 的目录名。
SESSION_ID=${SESSION_ID:-mb001_return_book_to_original_bookcase}

# 拼接范围（含首尾），多机运行时可以在启动命令前分别设置这两个变量。
# 起始实例编号，从 1 开始。
FIRST_INSTANCE=${FIRST_INSTANCE:-1}
# 结束实例编号，例如两台机器可分别处理 1–40 和 41–80。
LAST_INSTANCE=${LAST_INSTANCE:-80}

# GPU 与并发
# 解码序列化录制时 OmniGibson 使用的物理 GPU 编号，多张卡用空格分隔，例如 GPUS=(0 1)。
GPUS=(1)
# 每张卡同时运行的拼接进程上限，每个进程一次处理一个实例。
WORKERS_PER_GPU=${WORKERS_PER_GPU:-4}
# 相邻 worker 的启动间隔（秒），设为 0 可连续启动。
START_DELAY=${START_DELAY:-30}

cd "$REPO"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONBREAKPOINT=0 PYTHONUNBUFFERED=1 OMNIGIBSON_HEADLESS=1
SOURCE_CONFIG="$OUTPUT_ROOT/sessions/$SESSION_ID/config.yaml"
if [[ ! -f "$SOURCE_CONFIG" ]]; then
    printf 'Session config not found: %s\n' "$SOURCE_CONFIG" >&2
    exit 1
fi
if (( FIRST_INSTANCE < 1 || LAST_INSTANCE < FIRST_INSTANCE || WORKERS_PER_GPU < 1 || ${#GPUS[@]} == 0 )); then
    printf 'Set a valid instance range, worker count and GPU list.\n' >&2
    exit 2
fi

# 多个 compose 批次可共享输出目录；build 持有该目录的独占锁。
exec {run_lock}<"$OUTPUT_ROOT"
if ! flock -sn "$run_lock"; then
    printf 'Output directory is in use by another stage: %s\n' "$OUTPUT_ROOT" >&2
    exit 1
fi
REPORT_DIR="$OUTPUT_ROOT/reports/$SESSION_ID"
mkdir -p -- "$REPORT_DIR"
BATCH_LOG=$(mktemp "$REPORT_DIR/compose_episodes_$(date +%Y%m%d_%H%M%S)_XXXXXX.log")
printf 'Compose log: %s\n' "$BATCH_LOG" | tee -a -- "$BATCH_LOG"

source "$SCRIPT_DIR/runtime_cache.sh"
init_parallel_runtime
CONFIG="$RUN_STATE/config.yaml"
sed "s|^  output_root:.*|  output_root: $OUTPUT_ROOT|" "$SOURCE_CONFIG" > "$CONFIG"
printf '%s\n' "$FIRST_INSTANCE" > "$RUN_STATE/next_instance"
touch "$RUN_STATE/queue.lock"
pids=()
trap 'trap "" INT TERM; kill -TERM "${pids[@]}" 2>/dev/null || true; wait || true; exit 130' INT TERM

worker() {
    local gpu="$1" instance episode instance_log log_fd child_pid="" failed=0 status
    trap 'if [[ -n "$child_pid" ]]; then
        kill -TERM -- "-$child_pid" 2>/dev/null || true
        wait "$child_pid" || true
    fi; exit 130' INT TERM
    configure_parallel_worker
    export CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES="$gpu"
    export OMNIGIBSON_GPU_ID="$gpu" OMNIGIBSON_PHYSICS_GPU_ID=0

    exec 9>"$RUN_STATE/queue.lock"
    while true; do
        flock -x 9
        read -r instance < "$RUN_STATE/next_instance"
        if (( instance > LAST_INSTANCE )); then
            flock -u 9
            break
        fi
        printf '%s\n' "$((instance + 1))" > "$RUN_STATE/next_instance"
        flock -u 9

        printf -v instance_log '%s/compose_instance_%04d.log' "$REPORT_DIR" "$instance"
        printf -v episode 'episode_%08d' "$((instance - 1))"
        # 锁定实例日志，防止两个批次同时写入同一份 HDF5 和 recipe。
        exec {log_fd}>>"$instance_log"
        if flock -n "$log_fd"; then
            printf '[START] compose on GPU %s, instance %s\n' "$gpu" "$instance" | tee -a -- "$BATCH_LOG"
            setsid python -m memory_bench.episodes.compose_episodes "$CONFIG" \
                --instance-range "$instance-$instance" >&"$log_fd" 2>&1 &
            child_pid=$!
            if wait "$child_pid"; then
                if [[ -f "$OUTPUT_ROOT/recipes/$SESSION_ID/$episode.json" ]]; then
                    status=DONE
                else
                    status='SKIP: no recipe'
                fi
            else
                status="FAILED: exit $?, see $instance_log"
                failed=1
            fi
            child_pid=""
        else
            status='FAILED: instance locked by another batch'
            failed=1
        fi
        exec {log_fd}>&-
        printf '[%s] instance %s\n' "$status" "$instance" | tee -a -- "$BATCH_LOG"
    done
    return "$failed"
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
exit "$failed"
