# Performance Notes

Mini-Docker is intended to stay small and low-overhead enough to serve as the
runtime layer for a low-infrastructure self-hosted PaaS. Performance matters,
but current numbers should be treated as targets until they are reproduced on a
real Linux host with repeatable scripts.

## What To Measure

The important runtime metrics are:

| Metric | Why It Matters |
| --- | --- |
| cold container startup time | affects deployment and scale-to-zero latency |
| warm container startup time | affects restart speed and recovery |
| idle memory overhead | determines density on small servers |
| cgroup enforcement accuracy | protects the host from noisy workloads |
| cleanup time | affects failed deploy recovery |
| daemon API latency | affects PaaS controller responsiveness |

## Benchmark Environment Template

Record this before publishing any result:

```bash
uname -a
python3 --version
mount | grep cgroup
ip -V
iptables --version
free -h
lscpu
```

Suggested table:

| Component | Value |
| --- | --- |
| CPU | |
| Memory | |
| Kernel | |
| Distribution | |
| Python | |
| Storage | |
| Cgroup mode | |

## Startup Benchmark

```bash
#!/usr/bin/env bash
set -euo pipefail

ITERATIONS="${ITERATIONS:-50}"
TOTAL=0

for i in $(seq 1 "$ITERATIONS"); do
  START="$(date +%s%N)"
  sudo python3 -m mini_docker run --rm ./rootfs /bin/true >/dev/null
  END="$(date +%s%N)"
  ELAPSED="$(( (END - START) / 1000000 ))"
  TOTAL="$((TOTAL + ELAPSED))"
  echo "run=$i ms=$ELAPSED"
done

echo "average_ms=$((TOTAL / ITERATIONS)) iterations=$ITERATIONS"
```

## Resource Limit Checks

### Memory

```bash
sudo python3 -m mini_docker run --memory 50M ./rootfs /bin/sh -c '
  dd if=/dev/zero of=/tmp/test bs=1M count=100
'
```

Expected result: the workload should be constrained by the cgroup memory limit
and fail without destabilizing the host.

### CPU

```bash
sudo python3 -m mini_docker run --cpu 25 ./rootfs /bin/sh -c '
  timeout 10 sh -c "while true; do :; done"
'
```

Expected result: CPU usage should remain near the requested quota for the
container process.

### PID Limit

```bash
sudo python3 -m mini_docker run --pids 10 ./rootfs /bin/sh -c '
  for i in $(seq 1 50); do sleep 100 & done
  wait
'
```

Expected result: process creation should stop at the cgroup PID limit.

## Operational Benchmarks

For PaaS work, also measure:

- create/start/stop/remove API latency through the Unix socket daemon
- restart time for a crashed detached workload
- mount and cgroup cleanup after failed startup
- port publishing setup and teardown time
- memory overhead of the daemon process

## Publishing Results

Only publish benchmark numbers when they include:

- exact host environment
- exact command used
- number of iterations
- whether root or rootless mode was used
- whether networking and OverlayFS were enabled
- raw output or a checked-in evidence file

## Current Position

Mini-Docker is designed for low infrastructure overhead, but performance claims
should be earned with Linux evidence. Until benchmark artifacts are checked in,
treat this document as the measurement plan rather than a leaderboard.
