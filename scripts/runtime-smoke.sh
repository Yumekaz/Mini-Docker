#!/usr/bin/env bash
# WSL-safe runtime smoke test for Mini-Docker.
#
# Defaults to rootless mode and uses temporary Mini-Docker state under /tmp.
# Root mode is supported explicitly with --root, but should be run only on a
# disposable Linux VM/server until host cleanup is proven there.

set -euo pipefail

MODE="rootless"
ROOTFS="./rootfs"
KEEP_STATE=0

usage() {
    cat <<'EOF'
Usage: scripts/runtime-smoke.sh [--rootless|--root] [--rootfs PATH] [--keep-state]

Checks:
  - mini-docker doctor
  - foreground container execution
  - metadata listing
  - logs command
  - remove stopped container
  - runtime cleanup dry-run

This script does not run root mode unless --root is passed explicitly.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --rootless)
            MODE="rootless"
            ;;
        --root)
            MODE="root"
            ;;
        --rootfs)
            ROOTFS="${2:?missing rootfs path}"
            shift
            ;;
        --keep-state)
            KEEP_STATE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

if [ "$MODE" = "root" ] && [ "$(id -u)" -ne 0 ]; then
    echo "Root smoke mode requires root. Use --rootless on WSL/dev hosts." >&2
    exit 1
fi

STATE_DIR="$(mktemp -d /tmp/mini-docker-smoke.XXXXXX)"
RUN_DIR="${STATE_DIR}/run"
ROOT_DIR="${STATE_DIR}/root"
SMOKE_ROOTFS="${STATE_DIR}/rootfs"
mkdir -p "$RUN_DIR" "$ROOT_DIR" "$SMOKE_ROOTFS"

cleanup() {
    if [ "$KEEP_STATE" -eq 1 ]; then
        echo "Keeping smoke state at: $STATE_DIR"
        return
    fi
    rm -rf "$STATE_DIR"
}
trap cleanup EXIT

export MINI_DOCKER_ROOT="$ROOT_DIR"
export MINI_DOCKER_RUN="$RUN_DIR"

if [ ! -d "$ROOTFS" ]; then
    echo "Rootfs not found: $ROOTFS" >&2
    exit 1
fi

cp -a "$ROOTFS"/. "$SMOKE_ROOTFS"/
ROOTFS="$SMOKE_ROOTFS"

BASE_CMD=(python3 -m mini_docker)
RUN_FLAGS=(--no-overlay)
DOCTOR_FLAGS=()

if [ "$MODE" = "rootless" ]; then
    RUN_FLAGS=(--rootless "${RUN_FLAGS[@]}")
    DOCTOR_FLAGS=(--rootless)
fi

echo "[1/6] Checking host"
set +e
"${BASE_CMD[@]}" doctor "${DOCTOR_FLAGS[@]}" --rootfs "$ROOTFS"
DOCTOR_STATUS=$?
set -e
if [ "$DOCTOR_STATUS" -ne 0 ] && [ "$DOCTOR_STATUS" -ne 2 ]; then
    echo "Host check failed" >&2
    exit "$DOCTOR_STATUS"
fi

echo "[2/6] Running foreground workload"
RUN_OUTPUT="$("${BASE_CMD[@]}" run "${RUN_FLAGS[@]}" "$ROOTFS" /bin/echo mini-docker-smoke)"
echo "$RUN_OUTPUT"
echo "$RUN_OUTPUT" | grep -q "mini-docker-smoke"

CONTAINER_ID="$(echo "$RUN_OUTPUT" | awk '/Created container:/ {print $3}' | tail -n 1)"
if [ -z "$CONTAINER_ID" ]; then
    echo "Unable to find container id in run output" >&2
    exit 1
fi

echo "[3/6] Listing metadata"
"${BASE_CMD[@]}" ps -a --format json

echo "[4/6] Reading logs"
"${BASE_CMD[@]}" logs "$CONTAINER_ID" | grep -q "mini-docker-smoke"

echo "[5/6] Removing stopped container"
"${BASE_CMD[@]}" rm "$CONTAINER_ID"

echo "[6/6] Previewing runtime cleanup"
"${BASE_CMD[@]}" cleanup --runtime --dry-run --force

echo "runtime smoke passed ($MODE)"
