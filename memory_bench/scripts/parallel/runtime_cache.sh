# Shared disk caches for parallel MemoryBench jobs. Source after setting REPO.
CACHE_ROOT="$REPO/.cache/memory_bench_runtime"
TEXTURE_CACHE_MAX_KIB=$((8 * 1024 * 1024))

prune_texture_cache() {
    local texture_cache="$CACHE_ROOT/kit/texturecache" size
    [[ -d "$texture_cache" ]] || return 0
    size=$(du -sk -- "$texture_cache") || return 1
    if (( ${size%%$'\t'*} > TEXTURE_CACHE_MAX_KIB )); then
        printf 'Clearing the shared texture cache after it exceeded %s GiB.\n' "$((TEXTURE_CACHE_MAX_KIB / 1024 / 1024))"
        rm -rf -- "$texture_cache"
    fi
}

finish_parallel_runtime() {
    rm -rf -- "$RUN_STATE"
    if flock -xn "$cache_lock_fd"; then
        prune_texture_cache
    fi
    flock -u "$cache_lock_fd"
}

init_parallel_runtime() {
    mkdir -p "$CACHE_ROOT"/{kit,warp,cuda,triton,numba}
    exec {cache_lock_fd}>>"$CACHE_ROOT/cache.lock"
    # All batches hold a shared lock. Prune only when no other batch is using the cache.
    if flock -xn "$cache_lock_fd"; then
        prune_texture_cache
    fi
    flock -s "$cache_lock_fd"
    RUN_STATE=$(mktemp -d "$CACHE_ROOT/run.XXXXXX")
    trap finish_parallel_runtime EXIT
}

configure_parallel_worker() {
    local worker_dir="$RUN_STATE/worker_$BASHPID"
    mkdir -p "$worker_dir"/{appdata/global,tmp,xdg,torch}
    ln -s -- "$CACHE_ROOT/kit" "$worker_dir/appdata/global/cache"
    ln -s -- "$CACHE_ROOT/warp" "$worker_dir/appdata/global/warp_cache"

    export OMNIGIBSON_APPDATA_PATH="$worker_dir/appdata"
    export TMPDIR="$worker_dir/tmp" TMP="$worker_dir/tmp" TEMP="$worker_dir/tmp"
    export XDG_CACHE_HOME="$worker_dir/xdg"
    export CUDA_CACHE_PATH="$CACHE_ROOT/cuda"
    export TORCH_EXTENSIONS_DIR="$worker_dir/torch"
    export TRITON_CACHE_DIR="$CACHE_ROOT/triton"
    export NUMBA_CACHE_DIR="$CACHE_ROOT/numba"
}
