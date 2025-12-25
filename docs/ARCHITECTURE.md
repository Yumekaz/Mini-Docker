# Architecture

This document explains the internal architecture of Mini-Docker.

---

## Overview

Mini-Docker implements a defense-in-depth security model using 5 isolation layers:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              HOST SYSTEM                                     │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        MINI-DOCKER RUNTIME                           │   │
│  │                                                                      │   │
│  │   ┌──────────────────────────────────────────────────────────────┐  │   │
│  │   │                     SECURITY LAYERS                           │  │   │
│  │   │                                                               │  │   │
│  │   │  ┌─────────────────────────────────────────────────────────┐ │  │   │
│  │   │  │  Layer 5: OverlayFS         (Filesystem Isolation)      │ │  │   │
│  │   │  ├─────────────────────────────────────────────────────────┤ │  │   │
│  │   │  │  Layer 4: Capabilities      (Privilege Reduction)       │ │  │   │
│  │   │  ├─────────────────────────────────────────────────────────┤ │  │   │
│  │   │  │  Layer 3: Seccomp-BPF       (Syscall Filtering)        │ │  │   │
│  │   │  ├─────────────────────────────────────────────────────────┤ │  │   │
│  │   │  │  Layer 2: Cgroups v2        (Resource Limits)          │ │  │   │
│  │   │  ├─────────────────────────────────────────────────────────┤ │  │   │
│  │   │  │  Layer 1: Namespaces        (Resource Isolation)        │ │  │   │
│  │   │  └─────────────────────────────────────────────────────────┘ │  │   │
│  │   │                                                               │  │   │
│  │   │  ┌────────────┐  ┌────────────┐  ┌────────────┐              │  │   │
│  │   │  │ Container  │  │ Container  │  │ Container  │              │  │   │
│  │   │  │     1      │  │     2      │  │     3      │              │  │   │
│  │   │  └────────────┘  └────────────┘  └────────────┘              │  │   │
│  │   └──────────────────────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│                            LINUX KERNEL                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Module Breakdown

### Container Lifecycle

```
┌──────────────────────────────────────────────────────────────────┐
│                        container.py                               │
│                                                                  │
│   User Request                                                   │
│        │                                                         │
│        ▼                                                         │
│   ┌─────────┐    ┌─────────────┐    ┌──────────────┐            │
│   │ parse   │───▶│ create      │───▶│ apply        │            │
│   │ config  │    │ namespaces  │    │ cgroups      │            │
│   └─────────┘    └─────────────┘    └──────────────┘            │
│                                            │                     │
│                                            ▼                     │
│   ┌─────────┐    ┌─────────────┐    ┌──────────────┐            │
│   │ exec    │◀───│ drop        │◀───│ apply        │            │
│   │ command │    │ capabilities│    │ seccomp      │            │
│   └─────────┘    └─────────────┘    └──────────────┘            │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Module Details

### namespaces.py

Handles Linux namespace creation and management.

**Namespaces Implemented:**

| Namespace | Flag | Purpose |
|-----------|------|---------|
| PID | `CLONE_NEWPID` | Process isolation |
| UTS | `CLONE_NEWUTS` | Hostname isolation |
| Mount | `CLONE_NEWNS` | Filesystem isolation |
| IPC | `CLONE_NEWIPC` | Inter-process communication |
| Network | `CLONE_NEWNET` | Network stack isolation |
| User | `CLONE_NEWUSER` | UID/GID mapping |
| Cgroup | `CLONE_NEWCGROUP` | Cgroup hierarchy |

**Key Functions:**

```python
def create_namespaces(flags: int) -> int:
    """Create new namespaces using unshare()."""
    
def enter_namespace(pid: int, ns_type: str) -> None:
    """Enter an existing namespace via setns()."""
    
def setup_user_namespace(uid_map: str, gid_map: str) -> None:
    """Configure UID/GID mappings for user namespace."""
```

---

### cgroups.py

Manages cgroups v2 resource limits.

**Resource Limits:**

| Resource | File | Example |
|----------|------|---------|
| CPU | `cpu.max` | `50000 100000` (50%) |
| Memory | `memory.max` | `104857600` (100MB) |
| PIDs | `pids.max` | `50` |

**Key Functions:**

```python
def create_cgroup(name: str) -> str:
    """Create a new cgroup directory."""
    
def set_cpu_limit(cgroup: str, percent: int) -> None:
    """Set CPU limit as percentage."""
    
def set_memory_limit(cgroup: str, bytes: int) -> None:
    """Set memory limit in bytes."""
    
def add_process(cgroup: str, pid: int) -> None:
    """Add process to cgroup."""
```

**Cgroup Hierarchy:**

```
/sys/fs/cgroup/
└── mini-docker/
    ├── container-abc123/
    │   ├── cgroup.procs
    │   ├── cpu.max
    │   ├── memory.max
    │   └── pids.max
    └── container-def456/
        └── ...
```

---

### seccomp.py

Implements syscall filtering using seccomp-BPF.

**Filter Strategy:** Whitelist (default deny)

**Allowed Syscalls (~60):**

| Category | Examples |
|----------|----------|
| File I/O | `read`, `write`, `open`, `close` |
| Process | `exit`, `exit_group`, `getpid` |
| Memory | `mmap`, `munmap`, `brk` |
| Signals | `rt_sigaction`, `rt_sigprocmask` |
| Network | `socket`, `bind`, `connect` |

**Key Functions:**

```python
def create_filter() -> bytes:
    """Create BPF filter bytecode."""
    
def load_filter(filter: bytes) -> None:
    """Load filter into kernel."""
    
def get_allowed_syscalls() -> List[str]:
    """Get list of allowed syscalls."""
```

---

### capabilities.py

Manages Linux capabilities.

**Capability Sets:**

| Set | Purpose |
|-----|---------|
| Effective | Currently active capabilities |
| Permitted | Max capabilities available |
| Inheritable | Passed to child processes |
| Bounding | Upper limit on capabilities |
| Ambient | Preserved across execve() |

**Dropped Capabilities:**

- `CAP_SYS_ADMIN` - System administration
- `CAP_NET_ADMIN` - Network configuration
- `CAP_SYS_PTRACE` - Process tracing
- `CAP_SYS_MODULE` - Kernel module loading
- And many more...

**Key Functions:**

```python
def drop_capabilities() -> None:
    """Drop all dangerous capabilities."""
    
def set_no_new_privs() -> None:
    """Prevent privilege escalation."""
```

---

### filesystem.py

Manages OverlayFS and filesystem setup.

**OverlayFS Structure:**

```
container-root/
├── lower/      (read-only base image)
├── upper/      (container's changes)
├── work/       (OverlayFS internal)
└── merged/     (union mount point)
```

**Key Functions:**

```python
def setup_overlay(lower: str, container_id: str) -> str:
    """Create OverlayFS mount."""
    
def mount_special_filesystems(root: str) -> None:
    """Mount /proc, /sys, /dev."""
    
def pivot_root(new_root: str) -> None:
    """Change root filesystem."""
```

---

### network.py

Handles container networking.

**Network Architecture:**

```
┌─────────────────────────────────────────────────────────────┐
│                         HOST                                 │
│                                                             │
│  ┌─────────────┐         ┌─────────────┐                   │
│  │  eth0       │         │  mini-br0   │ (bridge)          │
│  │  (physical) │         │  10.0.0.1   │                   │
│  └──────┬──────┘         └──────┬──────┘                   │
│         │                       │                           │
│         │    NAT (iptables)     │                           │
│         ◀──────────────────────▶                            │
│                                 │                           │
│              ┌──────────────────┼──────────────────┐        │
│              │                  │                  │        │
│         ┌────┴────┐        ┌────┴────┐        ┌───┴────┐   │
│         │ veth0   │        │ veth1   │        │ veth2  │   │
│         │ (host)  │        │ (host)  │        │ (host) │   │
│         └────┬────┘        └────┬────┘        └───┬────┘   │
│              │                  │                  │        │
├──────────────┼──────────────────┼──────────────────┼────────┤
│              │                  │                  │        │
│         ┌────┴────┐        ┌────┴────┐        ┌───┴────┐   │
│         │ eth0    │        │ eth0    │        │ eth0   │   │
│         │10.0.0.2 │        │10.0.0.3 │        │10.0.0.4│   │
│         └─────────┘        └─────────┘        └────────┘   │
│         Container 1        Container 2        Container 3   │
└─────────────────────────────────────────────────────────────┘
```

**Key Functions:**

```python
def create_bridge(name: str, ip: str) -> None:
    """Create network bridge."""
    
def create_veth_pair(host: str, container: str) -> None:
    """Create virtual ethernet pair."""
    
def setup_nat(bridge: str, interface: str) -> None:
    """Configure NAT with iptables."""
```

---

### oci.py

Implements OCI runtime specification support.

**config.json Structure:**

```json
{
    "ociVersion": "1.0.0",
    "process": {
        "args": ["/bin/sh"],
        "env": ["PATH=/bin:/usr/bin"],
        "cwd": "/"
    },
    "root": {
        "path": "rootfs",
        "readonly": false
    },
    "linux": {
        "namespaces": [...],
        "resources": {...}
    }
}
```

---

## Data Flow

```
User Command
     │
     ▼
┌─────────┐
│  CLI    │  (cli.py)
└────┬────┘
     │
     ▼
┌─────────────┐
│  Container  │  (container.py)
└──────┬──────┘
       │
       ├───────────────┬───────────────┬───────────────┐
       ▼               ▼               ▼               ▼
┌───────────┐   ┌───────────┐   ┌───────────┐   ┌───────────┐
│ Namespace │   │  Cgroup   │   │ Filesystem│   │  Network  │
└───────────┘   └───────────┘   └───────────┘   └───────────┘
       │               │               │               │
       └───────────────┴───────────────┴───────────────┘
                              │
                              ▼
                      ┌───────────────┐
                      │ Apply Seccomp │
                      │ Drop Caps     │
                      └───────────────┘
                              │
                              ▼
                      ┌───────────────┐
                      │ Execute User  │
                      │ Command       │
                      └───────────────┘
```

---

## Design Decisions

### Why Python?

- Educational clarity over performance
- Easy to read and understand
- Extensive standard library
- Lower barrier to entry for learners

### Why Cgroups v2 Only?

- Unified hierarchy is simpler
- Better resource control semantics
- Modern Linux default
- Avoids complexity of v1/v2 hybrid

### Why Whitelist Seccomp?

- More secure than blacklist
- Clear about what's allowed
- Easier to audit
- Defense in depth

---

## Further Reading

- [Security Model](SECURITY-MODEL.md)
- [Linux Namespaces](https://man7.org/linux/man-pages/man7/namespaces.7.html)
- [Cgroups v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
- [OCI Runtime Spec](https://github.com/opencontainers/runtime-spec)
