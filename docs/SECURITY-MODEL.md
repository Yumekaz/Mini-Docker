# Security Model

Mini-Docker is being hardened as a lightweight runtime foundation for a
self-hosted PaaS. Its security model is based on Linux kernel isolation layers:
namespaces, cgroups, seccomp, capabilities, `NO_NEW_PRIVS`, and filesystem
separation.

This document describes the intended model and the current boundaries. It does
not claim that Mini-Docker is already audited or safe for arbitrary hostile
multi-tenant workloads.

## Security Goals

Mini-Docker aims to provide:

- process isolation from the host and other containers
- filesystem isolation through mount namespaces and isolated root filesystems
- resource containment through cgroups v2
- reduced syscall surface through seccomp-BPF
- reduced privilege through Linux capability filtering
- safer execution through `NO_NEW_PRIVS`
- optional rootless execution through user namespaces

## Threat Model

### In Scope

Mini-Docker is designed to reduce risk from:

- accidental process interference between workloads
- noisy-neighbor CPU, memory, and PID exhaustion
- filesystem writes escaping the configured rootfs
- simple privilege escalation attempts through unnecessary capabilities
- direct use of dangerous syscalls blocked by the seccomp policy
- network isolation gaps between container and host namespaces

### Not Yet In Scope

Mini-Docker should not yet be treated as complete protection against:

- kernel zero-days
- sophisticated container escape chains
- malicious public code execution at internet scale
- side-channel attacks
- malicious image supply-chain attacks
- compromised host root
- unreviewed multi-tenant production workloads

## Isolation Layers

### 1. Namespaces

Mini-Docker uses Linux namespaces to give a container its own view of selected
system resources.

| Namespace | Purpose |
| --- | --- |
| PID | Isolates process IDs and process visibility |
| UTS | Isolates hostname and domain name |
| Mount | Isolates mount points and filesystem view |
| IPC | Isolates System V IPC and POSIX message queues |
| Network | Isolates interfaces, routes, and ports |
| User | Maps container users away from host users |
| Cgroup | Isolates cgroup hierarchy visibility |

### 2. Cgroups v2

Cgroups limit and account for resource usage.

| Resource | Control File | Purpose |
| --- | --- | --- |
| CPU | `cpu.max` | Throttle CPU usage |
| Memory | `memory.max` | Enforce a hard memory ceiling |
| PIDs | `pids.max` | Limit process count and fork bombs |
| I/O | `io.max` | Future I/O throttling support |

For PaaS use, cgroup setup must be fail-closed. If limits cannot be installed,
the workload should not start in a production deployment.

### 3. Filesystem Isolation

Mini-Docker supports OverlayFS for copy-on-write container filesystems and
falls back to chroot where OverlayFS is unavailable.

OverlayFS is the preferred path for full Linux mode:

```text
lower/   read-only base rootfs
upper/   writable container changes
work/    OverlayFS work directory
merged/  runtime root view
```

`pivot_root` is preferred over chroot when available because it removes the old
root from the workload's view more cleanly.

### 4. Seccomp-BPF

Mini-Docker builds a seccomp whitelist and denies syscalls not explicitly
allowed by the policy.

High-risk syscalls intended to stay blocked include:

- `ptrace`
- `mount`
- `umount2`
- `reboot`
- `init_module`
- `finit_module`
- `kexec_load`
- `bpf`
- `perf_event_open`
- `setns`
- `unshare`

For PaaS hardening, seccomp installation failure must stop startup instead of
falling back silently.

### 5. Capabilities

Linux capabilities split root privileges into smaller units. Mini-Docker
reduces the active capability set before workload execution.

Capabilities that should not be available to normal app workloads include:

- `CAP_SYS_ADMIN`
- `CAP_NET_ADMIN`
- `CAP_SYS_PTRACE`
- `CAP_SYS_MODULE`
- `CAP_SYS_RAWIO`
- `CAP_SYS_BOOT`
- `CAP_BPF`
- `CAP_PERFMON`

PaaS workloads should eventually support profiles: minimal, web-service,
networked-service, and explicit custom capability grants.

### 6. NO_NEW_PRIVS

`NO_NEW_PRIVS` prevents privilege gains across `execve`, including setuid and
setgid escalation paths.

```c
prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
```

This should be enabled before seccomp filter installation and inherited by the
workload process.

## Rootless Mode

Rootless mode uses user namespaces to map an unprivileged host user to root
inside the container. This reduces host risk but limits some features:

- host-managed bridge networking is unavailable without elevated privileges
- cgroup enforcement depends on host delegation
- OverlayFS availability varies by kernel and distribution

Rootless mode is useful for development and safer local workloads, but it does
not automatically solve all multi-tenant security concerns.

## PaaS Hardening Checklist

Before Mini-Docker is exposed to untrusted users, the runtime should have:

- fail-closed behavior for namespace, cgroup, seccomp, capability, and mount failures
- Linux integration tests for root and rootless container startup
- tests proving cgroup limits actually constrain CPU, memory, and PIDs
- tests proving seccomp blocks dangerous syscalls
- deterministic cleanup for failed networking, cgroups, and mounts
- daemon socket permissions and authentication strategy
- image/rootfs validation and provenance checks
- restricted defaults for capabilities and writable mounts
- host hardening documentation
- external security review

## Practical Guidance

For now:

- use Mini-Docker for controlled servers, self-hosted experiments, and runtime development
- avoid public arbitrary-code execution until hardening is complete
- prefer rootless mode where full networking and cgroups are not required
- use strict resource limits for every workload
- keep the host kernel updated
- run workloads under a dedicated server user or dedicated VM where possible

Mini-Docker can become a serious PaaS runtime foundation, but security has to
be proven by behavior and tests, not by docs language.
