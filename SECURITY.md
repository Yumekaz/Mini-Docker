# Security Policy

## Reporting Vulnerabilities

Please do not open public issues for security vulnerabilities.

Report sensitive findings privately to the maintainer:

- Mihir Swarnkar - [@Yumekaz](https://github.com/Yumekaz)

## Scope

Mini-Docker is an experimental Linux container runtime foundation for a
low-infrastructure PaaS. It uses real kernel isolation primitives, including
namespaces, cgroups v2, seccomp filtering, Linux capabilities, and filesystem
isolation.

It is not yet a hardened or audited production runtime for arbitrary untrusted
multi-tenant workloads. The goal is to move toward that standard, but the
current project should be treated as a serious runtime prototype that still
needs Linux validation, fail-closed security behavior, and external review.

## Current Security Position

Mini-Docker aims to provide:

- process, mount, IPC, UTS, network, user, and cgroup namespace isolation
- cgroups v2 CPU, memory, and PID limiting
- seccomp-BPF syscall filtering
- Linux capability reduction
- `NO_NEW_PRIVS`-based escalation prevention
- isolated root filesystems through OverlayFS or chroot fallback

For now, do not expose Mini-Docker directly to unknown users or run arbitrary
internet-submitted workloads without an additional isolation boundary, strong
host hardening, and a reviewed deployment model.

## Production Guidance

For highly hostile workloads, compare Mini-Docker against hardened runtimes and
sandboxing layers such as `runc`, `crun`, gVisor, Kata Containers, Firecracker,
or full VM isolation. Mini-Docker can become the runtime layer of a self-hosted
PaaS, but that should happen through measured hardening rather than a branding
claim.
