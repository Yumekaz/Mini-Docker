# 🐳 Mini-Docker

**A Fully Functional Container Runtime Built from Scratch in Python**

Mini-Docker is transitioning into a robust, lightweight container runtime serving as the foundation for a scalable, Stateful-first Platform-as-a-Service (PaaS).

---

## What is Mini-Docker?

Mini-Docker implements the core technologies powering Docker and Podman directly at the Linux kernel level using pure Python and raw syscalls. 

Originally built as an educational tool, it is now the **default runtime backend and engine** for self-hosted backend PaaS platforms. It provides a clean runtime abstraction for deploying, running, and monitoring APIs, workers, databases, and stateful services on low-resource Linux hardware.

**Why Mini-Docker?**

| Challenge | Solution |
|-----------|----------|
| Heavy orchestration overhead | Clean, low-footprint Python implementation |
| Hard to integrate with custom PaaS | Built-in REST API Daemon via Unix Socket |
| Black-box failure states | Honest Ops: precise exit codes and logs |

---

## ✨ Features

### Core Features

| Feature | Description |
|---------|-------------|
| **Namespace Isolation** | All 7 Linux namespaces (PID, UTS, Mount, IPC, Network, User, Cgroup) |
| **Cgroups v2** | Resource limiting (CPU, memory, process count) |
| **Virtual Networking** | Veth pairs with bridge, NAT, and `iptables` port publishing |
| **REST API Daemon** | Unix Domain Socket API for programmatic PaaS control |
| **OverlayFS** | Copy-on-write filesystem for efficient storage |
| **Honest Ops** | Accurate process exit code tracking and reliable restart policies |
| **Pod Support** | Kubernetes-style pod grouping and shared namespaces |
| **Rootless Mode** | Run containers without root via user namespaces |

### Security Layers

```text
Layer 1: Namespaces      → Process isolation (7 namespace types)
Layer 2: Cgroups v2      → Resource limits (CPU, memory, PIDs)
Layer 3: Seccomp-BPF     → Syscall filtering (~50 whitelisted calls)
Layer 4: Capabilities    → Privilege reduction (drop dangerous caps)
Layer 5: NO_NEW_PRIVS    → Escalation prevention (prctl flag)
```

---

## 🚀 Quick Start

### Prerequisites

| Requirement | Minimum | Check Command |
|-------------|---------|---------------|
| Linux Kernel | 4.18+ | `uname -r` |
| Python | 3.7+ | `python3 --version` |
| Root access | Yes (or rootless) | `whoami` |

### Installation

```bash
# Clone the repository
git clone https://github.com/Yumekaz/Mini-Docker.git
cd Mini-Docker

# Run setup script (creates rootfs and bridges)
sudo ./scripts/setup.sh

# Verify installation
python3 -m mini_docker --help
```

---

## 💻 Usage

### Basic Commands

```bash
# Run a simple command
sudo python3 -m mini_docker run ./rootfs /bin/echo "Hello World"

# Run a detached background service
sudo python3 -m mini_docker run -d --name web ./rootfs /bin/sleep 3600
```

### 🔌 Port Publishing & Networking

Expose your containers to the host (and the internet) using the `--publish` flag. This uses `iptables` NAT PREROUTING to route external traffic into the container's isolated veth network.

```bash
# Map host port 8080 to container port 80
sudo python3 -m mini_docker run -d -p 8080:80 --name web-server ./rootfs /bin/sh
```

### 🔄 Container Lifecycle & Honest Ops

Mini-Docker accurately tracks why processes fail by reaping zombies and persisting the `exit_code`. This allows a supervising PaaS to make smart restart and rollback decisions.

```bash
# List all containers (shows status and accurate exit codes)
sudo python3 -m mini_docker ps -a

# Restart a crashed or stopped container
sudo python3 -m mini_docker restart web-server

# Fetch logs from a container
sudo python3 -m mini_docker logs web-server
```

### 🤖 The REST API Daemon (For PaaS Integrations)

Mini-Docker can run as a background daemon listening on a Unix Domain Socket, allowing external applications (like a PaaS control plane) to manage containers programmatically via HTTP requests, exactly like `/var/run/docker.sock`.

```bash
# Start the daemon
sudo python3 -m mini_docker daemon --socket /var/run/mini-docker.sock
```

*Example API calls from your PaaS controller:*
- `GET /containers/json`
- `POST /containers/create`
- `POST /containers/{id}/start`
- `POST /containers/{id}/restart`

### Resource Limits

```bash
# Limit memory to 100MB and CPU to 50%
sudo python3 -m mini_docker run \
    --memory 100M \
    --cpu 50 \
    --pids 10 \
    ./rootfs /bin/sh
```

### Pod Support (Kubernetes-style)

```bash
# Create a pod
sudo python3 -m mini_docker pod create mypod

# Add containers to pod (shared namespaces)
sudo python3 -m mini_docker pod add mypod --name app ./rootfs /bin/sh
```

---

## 📐 Project Structure

```text
Mini-Docker/
├── mini_docker/           # Core runtime engine code
│   ├── cli.py            # Command-line interface
│   ├── daemon.py         # Unix Socket REST API Server
│   ├── container.py      # Container lifecycle (start, stop, restart)
│   ├── namespaces.py     # Linux namespaces
│   ├── cgroups.py        # Cgroups v2
│   ├── filesystem.py     # OverlayFS & chroot
│   ├── network.py        # Virtual networking & iptables port mapping
│   ├── metadata.py       # State management & exit code tracking
│   └── ...
├── docs/                 # Documentation
├── tests/                # Unit tests
└── scripts/              # Setup and teardown utilities
```

---

## 🔒 Security Notice

Mini-Docker is transitioning into a production-capable runtime adapter for lightweight PaaS environments. However, it relies heavily on local Linux primitives and namespace isolation. When running multi-tenant workloads, always pair it with the built-in Seccomp and Capability dropping mechanisms, and avoid running untrusted binaries with elevated host privileges.

---

## 🛠️ Development

```bash
# Run tests
pytest tests/ -v

# Run linter & formatter
flake8 mini_docker/
black mini_docker/ tests/
```

---

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.
