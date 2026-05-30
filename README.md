# Mini-Docker

**A lightweight Linux container runtime built from scratch in Python.**

Mini-Docker is being developed as the runtime foundation for a
low-infrastructure self-hosted PaaS. The goal is to run backend services,
workers, APIs, and stateful workloads on small Linux servers without depending
on Docker, containerd, or runc for the core lifecycle path.

The runtime still works close to the kernel: namespaces, cgroups, mounts,
seccomp, capabilities, veth networking, and process lifecycle management are
handled directly through Python, `ctypes`, libc calls, and Linux userspace
interfaces.

## Current Status

Mini-Docker is a serious runtime prototype moving toward a PaaS backend.

It is not yet an audited production runtime for arbitrary untrusted
multi-tenant workloads. The right current use is controlled Linux-server
experimentation, trusted or semi-trusted workloads, and building the surrounding
PaaS control plane while hardening the runtime.

## Why Mini-Docker Exists

| Goal | Runtime Direction |
| --- | --- |
| Low infrastructure overhead | Keep the runtime small, direct, and Linux-native |
| PaaS integration | Expose lifecycle operations through a Unix-socket API |
| Operational clarity | Track container metadata, logs, status, and exit codes |
| Security hardening | Layer namespaces, cgroups, seccomp, capabilities, and rootless mode |
| Runtime ownership | Build the core lifecycle path without high-level container runtimes |

## Features

| Area | Implemented Capability |
| --- | --- |
| Container lifecycle | create, start, stop, restart, remove, inspect, exec, logs |
| Namespaces | PID, UTS, mount, IPC, network, user, and cgroup namespace support |
| Resource limits | cgroups v2 CPU, memory, and PID limits |
| Filesystem | OverlayFS copy-on-write support with chroot fallback |
| Networking | bridge, veth pair setup, NAT, and host port publishing |
| Security | seccomp-BPF filtering, capability reduction, `NO_NEW_PRIVS` |
| API | Unix Domain Socket HTTP daemon for external controllers |
| Pods | shared namespace grouping for sidecar-style workloads |
| OCI | basic OCI bundle parsing and execution path |
| Images | simple `Imagefile` builder for local rootfs-based images |

## Architecture

```text
PaaS controller or CLI
        |
        v
Mini-Docker daemon / CLI
        |
        v
Container manager
        |
        +-- namespaces
        +-- cgroups v2
        +-- filesystem setup
        +-- veth / bridge networking
        +-- seccomp filter
        +-- capability drop
        |
        v
Container process
```

## Requirements

| Requirement | Minimum |
| --- | --- |
| OS | Linux |
| Kernel | 4.18+ recommended |
| Python | 3.8+ |
| Privileges | root for full mode, user namespaces for rootless mode |
| System tools | `ip`, `iptables`, `mount`, `chroot` for full feature paths |

Windows is fine for editing and pure Python checks, but real runtime behavior
must be validated on Linux because the project depends on Linux-only kernel
features.

## Development Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The package installs a `mini-docker` console command:

```bash
mini-docker --help
```

## Quick Start

```bash
git clone https://github.com/Yumekaz/Mini-Docker.git
cd Mini-Docker

sudo ./scripts/setup.sh
python3 -m mini_docker --help
```

Run a basic container:

```bash
sudo python3 -m mini_docker run ./rootfs /bin/echo "Hello from Mini-Docker"
```

Run a detached service:

```bash
sudo python3 -m mini_docker run -d --name web ./rootfs /bin/sleep 3600
sudo python3 -m mini_docker ps -a
sudo python3 -m mini_docker logs web
```

Run with resource limits:

```bash
sudo python3 -m mini_docker run \
  --memory 100M \
  --cpu 50 \
  --pids 20 \
  ./rootfs /bin/sh
```

Publish a port:

```bash
sudo python3 -m mini_docker run -d \
  --name web-server \
  --publish 8080:80 \
  ./rootfs /bin/sh
```

Start the daemon for a PaaS control plane:

```bash
sudo python3 -m mini_docker daemon --socket /var/run/mini-docker.sock
```

Check host compatibility before running root-mode containers:

```bash
python3 -m mini_docker doctor --rootless
python3 -m mini_docker doctor
```

Preview cleanup of Mini-Docker runtime leftovers:

```bash
python3 -m mini_docker cleanup --runtime --dry-run
```

Repair only the bundled rootfs without touching host networking or cgroups:

```bash
./scripts/setup.sh --rootfs-only
```

Run the WSL-safe rootless smoke test:

```bash
scripts/runtime-smoke.sh --rootless
```

Supported daemon endpoints include:

- `GET /containers/json`
- `GET /containers/{id}/json`
- `POST /containers/create`
- `POST /containers/{id}/start`
- `POST /containers/{id}/restart`
- `POST /containers/{id}/stop`
- `DELETE /containers/{id}`

## Rootless Mode

Mini-Docker includes rootless support through user namespaces:

```bash
python3 -m mini_docker run --rootless ./rootfs /bin/sh
```

Rootless mode is useful for development and safer local experimentation. Some
features are naturally limited: bridge networking, strict cgroup enforcement,
and OverlayFS may require root or host-specific configuration.

## Project Layout

```text
mini_docker/
  cli.py            command-line interface
  daemon.py         Unix-socket HTTP API
  container.py      lifecycle orchestration
  namespaces.py     Linux namespace wrappers
  cgroups.py        cgroups v2 resource controls
  filesystem.py     OverlayFS, chroot, pivot_root helpers
  network.py        bridge, veth, NAT, port forwarding
  seccomp.py        seccomp-BPF whitelist filter
  capabilities.py   Linux capability handling
  metadata.py       container state and lookup
  pod.py            pod-style shared namespace support
  oci.py            OCI bundle support
```

## Roadmap Toward A PaaS Runtime

The next hardening work should focus on:

- fail-closed startup when cgroups, seccomp, capabilities, or namespace setup fails
- Linux integration tests that actually run containers under root and rootless modes
- stronger daemon authentication and socket permission handling
- health checks, restart supervision, and deployment metadata
- deterministic cleanup of networking, mounts, cgroups, and process state
- image provenance, rootfs validation, and supply-chain checks
- documented threat model for trusted, semi-trusted, and untrusted workloads

## Security Notice

Mini-Docker uses real isolation mechanisms, but it has not yet gone through a
professional security audit, fuzzing campaign, or hostile multi-tenant review.
Do not expose it as an open public compute platform until the hardening roadmap
is complete and independently reviewed.

## License

MIT License. See [LICENSE](LICENSE).
