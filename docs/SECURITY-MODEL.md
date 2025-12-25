# Security Model

This document explains Mini-Docker's security architecture in detail.

---

## ⚠️ Disclaimer

**Mini-Docker is for educational purposes only.**

While it implements real security mechanisms, it has NOT been:
- Professionally audited
- Fuzz tested
- Penetration tested

For production workloads, use [runc](https://github.com/opencontainers/runc), [crun](https://github.com/containers/crun), or other hardened runtimes.

---

## Defense in Depth

Mini-Docker implements 5 independent security layers. Each layer provides protection even if other layers fail:

```
┌─────────────────────────────────────────────────────────────┐
│                    CONTAINER PROCESS                         │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Layer 5: NO_NEW_PRIVS                               │   │
│  │  ───────────────────                                 │   │
│  │  Prevents setuid/setgid escalation                   │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Layer 4: Capabilities                               │   │
│  │  ────────────────────                                │   │
│  │  Drops all dangerous capabilities                    │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Layer 3: Seccomp-BPF                                │   │
│  │  ────────────────────                                │   │
│  │  Whitelist ~60 safe syscalls only                    │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Layer 2: Cgroups v2                                 │   │
│  │  ──────────────────                                  │   │
│  │  Resource limits (CPU, memory, PIDs)                 │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Layer 1: Namespaces                                 │   │
│  │  ──────────────────                                  │   │
│  │  Process, filesystem, network, user isolation        │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                  │
└──────────────────────────┼──────────────────────────────────┘
                           │
                           ▼
                    LINUX KERNEL
```

---

## Layer 1: Namespaces

Namespaces provide resource isolation by giving containers their own view of system resources.

### Implemented Namespaces

| Namespace | Isolates | Attack Prevented |
|-----------|----------|------------------|
| **PID** | Process IDs | Can't see/signal host processes |
| **UTS** | Hostname | Can't discover host identity |
| **Mount** | Filesystem | Can't access host files |
| **IPC** | Shared memory | Can't access host IPC |
| **Network** | Network stack | Can't sniff host network |
| **User** | UID/GID | Root in container ≠ root on host |
| **Cgroup** | Cgroup view | Can't see host cgroup structure |

### Example: PID Isolation

```
HOST VIEW:                      CONTAINER VIEW:
┌──────────────────────────┐    ┌──────────────────────────┐
│ PID 1    - systemd       │    │ PID 1    - /bin/sh       │
│ PID 234  - sshd          │    │ PID 2    - /bin/sleep    │
│ PID 567  - nginx         │    │                          │
│ PID 1000 - container     │ => │ (only sees its own PIDs) │
│   └─ PID 1001 - sh       │    │                          │
│   └─ PID 1002 - sleep    │    │                          │
└──────────────────────────┘    └──────────────────────────┘
```

---

## Layer 2: Cgroups v2

Cgroups prevent resource exhaustion attacks.

### Resource Limits

| Resource | Limit | Protection |
|----------|-------|------------|
| **CPU** | Percentage or quota | Prevents CPU monopolization |
| **Memory** | Hard limit in bytes | Prevents OOM on host |
| **PIDs** | Maximum process count | Prevents fork bombs |

### Example: Fork Bomb Protection

Without cgroups:
```bash
:(){ :|:& };:  # Fork bomb crashes the system
```

With pids.max=50:
```bash
:(){ :|:& };:  # Fails after 50 processes
# Container can't create more processes
```

---

## Layer 3: Seccomp-BPF

Seccomp filters syscalls at the kernel level using BPF (Berkeley Packet Filter).

### Filter Strategy: Whitelist

```
┌─────────────────────────────────────────────┐
│           SYSCALL FILTER                     │
│                                             │
│   ┌─────────────────────────────────────┐   │
│   │         ALLOWED (~60 syscalls)       │   │
│   │                                      │   │
│   │  read, write, open, close, stat     │   │
│   │  mmap, brk, exit, getpid            │   │
│   │  socket, bind, listen, accept       │   │
│   │  ...                                │   │
│   └─────────────────────────────────────┘   │
│                                             │
│   ┌─────────────────────────────────────┐   │
│   │         BLOCKED (everything else)    │   │
│   │                                      │   │
│   │  ptrace, mount, reboot, init_module │   │
│   │  kexec_load, syslog, acct           │   │
│   │  ...                                │   │
│   └─────────────────────────────────────┘   │
│                                             │
└─────────────────────────────────────────────┘
```

### Dangerous Syscalls Blocked

| Syscall | Risk |
|---------|------|
| `ptrace` | Debug/control other processes |
| `mount` | Modify filesystem |
| `reboot` | Shutdown system |
| `init_module` | Load kernel modules |
| `kexec_load` | Replace running kernel |
| `perf_event_open` | Performance monitoring abuse |

---

## Layer 4: Capabilities

Linux capabilities break up root privileges into distinct units.

### Capabilities Dropped

| Capability | Risk if Kept |
|------------|--------------|
| `CAP_SYS_ADMIN` | Broad system control |
| `CAP_NET_ADMIN` | Network configuration |
| `CAP_SYS_PTRACE` | Process tracing |
| `CAP_SYS_MODULE` | Kernel modules |
| `CAP_MKNOD` | Create device nodes |
| `CAP_SYS_RAWIO` | Raw I/O port access |
| `CAP_SYS_BOOT` | Reboot system |
| `CAP_DAC_OVERRIDE` | Bypass file permissions |

### Minimal Capability Set

After dropping, container has only:
- `CAP_CHOWN` - Change file ownership
- `CAP_SETUID` - Set UID (within namespace)
- `CAP_SETGID` - Set GID (within namespace)
- `CAP_KILL` - Send signals (within namespace)

---

## Layer 5: NO_NEW_PRIVS

Prevents privilege escalation via setuid binaries.

### How It Works

```c
prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
```

Once set:
- Setuid/setgid bits are ignored
- Ambient capabilities can't be raised
- Inherited by all child processes
- Cannot be unset

### Attack Prevented

Without NO_NEW_PRIVS:
```bash
# Attacker could exploit setuid binary
./setuid-root-binary  # Gets root privileges
```

With NO_NEW_PRIVS:
```bash
./setuid-root-binary  # Runs as current user, no escalation
```

---

## Threat Model

### What Mini-Docker Protects Against

| Threat | Protection | Mechanism |
|--------|------------|-----------|
| ✅ **Noisy neighbor** | Can't hog resources | Cgroups limits |
| ✅ **Process snooping** | Can't see host processes | PID namespace |
| ✅ **Filesystem tampering** | Can't modify host files | Mount namespace + OverlayFS |
| ✅ **Network sniffing** | Can't see host traffic | Network namespace |
| ✅ **Fork bomb** | Limited processes | pids.max cgroup |
| ✅ **Memory exhaustion** | Limited memory | memory.max cgroup |
| ✅ **Privilege escalation** | Capabilities dropped | NO_NEW_PRIVS |
| ✅ **Dangerous syscalls** | Blocked at kernel | Seccomp-BPF |

### What Mini-Docker Does NOT Protect Against

| Threat | Reason | Mitigation |
|--------|--------|------------|
| ❌ **Kernel zero-days** | No hypervisor layer | Keep kernel updated |
| ❌ **Container escapes** | Not hardened | Use gVisor/Kata for untrusted |
| ❌ **Side-channel attacks** | No CPU isolation | Use VMs for sensitive data |
| ❌ **Malicious images** | No image scanning | Scan with trivy/grype |
| ❌ **Supply chain attacks** | No verification | Use signed images |
| ❌ **Sophisticated attackers** | Not audited | Don't run untrusted code |

---

## Security Checklist

When using Mini-Docker, always:

- [ ] **Set resource limits**
  ```bash
  --memory 100M --cpu 50 --pids-limit 50
  ```

- [ ] **Keep kernel updated**
  - Check for security patches regularly
  - Use LTS kernel when possible

- [ ] **Don't run untrusted code**
  - Only run containers you built
  - Review third-party images

- [ ] **Use rootless when possible**
  ```bash
  python3 -m mini_docker run --rootless ./rootfs /bin/sh
  ```

- [ ] **Monitor container activity**
  - Check logs for anomalies
  - Review seccomp violations

- [ ] **Limit capabilities further if possible**
  - Some workloads need even fewer capabilities

---

## Comparison with Production Runtimes

| Feature | Mini-Docker | runc | gVisor | Kata |
|---------|-------------|------|--------|------|
| Namespaces | ✅ | ✅ | ✅ | ✅ |
| Cgroups | ✅ | ✅ | ✅ | ✅ |
| Seccomp | ✅ | ✅ | ✅ | ✅ |
| Capabilities | ✅ | ✅ | ✅ | ✅ |
| Security audit | ❌ | ✅ | ✅ | ✅ |
| Kernel isolation | ❌ | ❌ | ✅ | ✅ |
| Production ready | ❌ | ✅ | ✅ | ✅ |

---

## Resources

- [Linux Namespaces](https://man7.org/linux/man-pages/man7/namespaces.7.html)
- [Cgroups v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
- [Seccomp-BPF](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html)
- [Linux Capabilities](https://man7.org/linux/man-pages/man7/capabilities.7.html)
- [Container Security Best Practices](https://sysdig.com/learn-cloud-native/container-security-best-practices/)

---

## Reporting Security Issues

Found a vulnerability? See [SECURITY.md](../SECURITY.md) for responsible disclosure guidelines.
