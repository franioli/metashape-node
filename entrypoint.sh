#!/bin/bash
# Container entrypoint: starts Metashape as a cluster node or as a standalone shell.
#   MODE=node        -> connect to the Metashape server (METASHAPE_HOST) as a processing node
#   MODE=standalone  -> keep the container alive; open a shell with `docker compose exec`
#   anything else    -> passed through as a command (e.g. `docker compose run ... bash`)
set -e

# GPU selection: use GPU_MASK if given, else enable every GPU the container can see.
# GPU_MASK is a bitmask: 1 = first GPU, 2 = second, 3 = first two, 5 = first and third...
GPU_ARGS=()
if [ -n "$GPU_MASK" ]; then
    GPU_ARGS=(--gpu_mask "$GPU_MASK" --cpu_enable "${CPU_ENABLE:-1}")
elif command -v nvidia-smi >/dev/null && N=$(nvidia-smi -L 2>/dev/null | wc -l) && [ "$N" -gt 0 ]; then
    GPU_ARGS=(--gpu_mask "$(( (1 << N) - 1 ))" --cpu_enable "${CPU_ENABLE:-1}")
    echo "Detected $N GPU(s), gpu_mask=$(( (1 << N) - 1 ))"
else
    echo "No GPU detected, running on CPU only"
fi

case "${1:-$MODE}" in
    node)
        exec metashape -platform offscreen --node \
            --host "${METASHAPE_HOST:?METASHAPE_HOST is required in node mode}" \
            --root "${METASHAPE_ROOT:-/workspace/}" \
            "${GPU_ARGS[@]}" --priority "${NODE_PRIORITY:-5}" ;;
    standalone)
        echo "Standalone mode: run 'docker compose exec metashape bash', then e.g."
        echo "  metashape -r script.py     (headless script)"
        exec sleep infinity ;;
    *)
        exec "$@" ;;
esac
