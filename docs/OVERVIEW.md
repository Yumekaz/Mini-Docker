# Mini-Docker Overview

Mini-Docker is a lightweight Linux container runtime built from scratch in
Python. Its current direction is to become the runtime layer for a
low-infrastructure self-hosted PaaS: a server environment that can start,
isolate, supervise, and clean up backend workloads without depending on Docker
or another high-level container runtime for the core lifecycle.

The current codebase is maintained and hardened as a standalone runtime
prototype.

## Runtime Positioning

Mini-Docker is best described as:

- a kernel-facing Linux runtime implemented in Python
- a small control surface for running service processes in isolated root filesystems
- a PaaS runtime foundation for trusted and semi-trusted workloads today
- a candidate for broader untrusted workload support after more hardening and Linux validation

It should not yet be described as audited, production-hardened, or ready for
arbitrary public multi-tenant compute.

## Core Capabilities

| Component | Purpose |
| --- | --- |
| `container.py` | Coordinates container create/start/stop/restart/remove flows |
| `namespaces.py` | Wraps `unshare` and `setns` for Linux namespace isolation |
| `cgroups.py` | Creates cgroups v2 resource limits for CPU, memory, and PIDs |
| `filesystem.py` | Sets up OverlayFS, chroot, `pivot_root`, and special filesystems |
| `network.py` | Creates bridges, veth pairs, NAT, and port forwarding |
| `seccomp.py` | Builds and installs a seccomp-BPF syscall whitelist |
| `capabilities.py` | Reduces Linux capability sets before workload execution |
| `daemon.py` | Exposes lifecycle operations over a Unix Domain Socket HTTP API |
| `metadata.py` | Persists runtime state, names, logs, exit codes, and lookup data |
| `pod.py` | Groups related containers with shared namespaces |
| `oci.py` | Loads and maps basic OCI bundle configuration |

## Operating Modes

### Full Linux Mode

Full mode runs with root privileges and can use the strongest current feature
set:

- namespace creation
- cgroups v2 enforcement
- OverlayFS mounts
- bridge and veth networking
- NAT and port publishing
- seccomp and capability filtering

```bash
sudo python3 -m mini_docker run --memory 128M --cpu 50 ./rootfs /bin/sh
```

### Rootless Mode

Rootless mode uses user namespaces and user-owned storage paths. It is useful
for local development and lower-risk experimentation, but some features depend
on host configuration and may be limited:

- bridge networking usually requires root
- strict cgroup enforcement may not be available
- OverlayFS may fall back to direct chroot behavior

```bash
python3 -m mini_docker run --rootless ./rootfs /bin/sh
```

## Runtime Flow

```text
CLI or daemon request
        |
        v
Create container metadata
        |
        v
Fork runtime child
        |
        v
Create / enter namespaces
        |
        v
Configure cgroups and networking
        |
        v
Prepare root filesystem
        |
        v
Drop capabilities and apply seccomp
        |
        v
exec workload
```

## PaaS Direction

The long-term goal is to use Mini-Docker as the runtime engine beneath a
self-hosted PaaS. That PaaS layer would own scheduling, deployments, routing,
build orchestration, health checks, secrets, logs, and user-facing APIs, while
Mini-Docker owns local workload isolation and lifecycle control.

Useful next layers:

- authenticated control-plane API
- reverse proxy and port allocation
- service health checks
- restart and rollback supervision
- per-app resource policies
- deployment logs and runtime events
- cleanup of failed mounts, cgroups, and network state

## Current Boundaries

The codebase already touches real Linux primitives, but several areas still
need hardening before hostile multi-tenant use:

- startup should fail closed when cgroups, seccomp, capabilities, or namespace setup fails
- Linux integration tests must prove actual namespace/cgroup/seccomp behavior
- daemon access must be locked down by socket permissions and authentication strategy
- rootfs and image inputs need validation and provenance controls
- cleanup must be reliable after partial startup failures
- security posture needs external review before public untrusted workloads

## Related Docs

- [Architecture](ARCHITECTURE.md)
- [Security Model](SECURITY-MODEL.md)
- [Quick Start](QUICKSTART.md)
- [CLI Commands](CLI-COMMANDS.md)
- [Performance Notes](BENCHMARKS.md)
