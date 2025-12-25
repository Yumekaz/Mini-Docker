# 📖 CLI Commands Reference

Complete reference for all Mini-Docker command-line commands.

---

## Table of Contents

- [Global Options](#global-options)
- [Container Commands](#container-commands)
  - [run](#run)
  - [run-oci](#run-oci)
  - [exec](#exec)
  - [ps](#ps)
  - [stop](#stop)
  - [rm](#rm)
  - [logs](#logs)
  - [inspect](#inspect)
- [Image Commands](#image-commands)
  - [build](#build)
  - [images](#images)
  - [rmi](#rmi)
- [Pod Commands](#pod-commands)
  - [pod create](#pod-create)
  - [pod add](#pod-add)
  - [pod ls](#pod-ls)
  - [pod inspect](#pod-inspect)
  - [pod rm](#pod-rm)
- [System Commands](#system-commands)
  - [info](#info)
  - [version](#version)
  - [cleanup](#cleanup)
- [Root Commands (Require sudo)](#root-commands-require-sudo)
- [Safe Commands (Rootless Mode)](#safe-commands-rootless-mode)

---

## Global Options

These options apply to all commands:

| Option | Short | Description |
|--------|-------|-------------|
| `--help` | `-h` | Show help message |
| `--version` | `-v` | Show version |
| `--debug` | | Enable debug mode |
| `--quiet` | `-q` | Suppress output |

```bash
# Examples
python3 -m mini_docker --help
python3 -m mini_docker --version
python3 -m mini_docker --debug run ./rootfs /bin/sh
```

---

## Container Commands

### run

Run a new container.

```bash
python3 -m mini_docker run [OPTIONS] ROOTFS COMMAND [ARGS...]
```

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--name` | `-n` | auto | Container name |
| `--hostname` | `-H` | container | Container hostname |
| `--memory` | `-m` | unlimited | Memory limit (e.g., 100M, 1G) |
| `--cpu` | `-c` | 100 | CPU limit percentage (1-100) |
| `--pids-limit` | | unlimited | Max number of processes |
| `--pids` | | unlimited | Alias for `--pids-limit` |
| `--net` | | disabled | Enable networking |
| `--rootless` | | false | Run without root |
| `--detach` | `-d` | false | Run in background |
| `--interactive` | `-i` | false | Keep STDIN open |
| `--tty` | `-t` | false | Allocate a pseudo-TTY |
| `--rm` | | false | Remove container after exit |
| `--env` | `-e` | | Set environment variable |
| `--volume` | `-V` | | Bind mount a volume (host:container) |
| `--workdir` | `-w` | / | Working directory |
| `--user` | `-u` | root | User to run as |
| `--no-overlay` | | false | Use chroot instead of OverlayFS |
| `--pod` | | | Run container inside a pod |

#### Examples

```bash
# Basic interactive shell
sudo python3 -m mini_docker run ./rootfs /bin/sh

# Named container with hostname
sudo python3 -m mini_docker run --name web --hostname webserver ./rootfs /bin/sh

# With resource limits
sudo python3 -m mini_docker run \
    --memory 100M \
    --cpu 50 \
    --pids-limit 20 \
    ./rootfs /bin/sh

# Run command and exit
sudo python3 -m mini_docker run ./rootfs /bin/echo "Hello World"

# Detached mode
sudo python3 -m mini_docker run -d --name background ./rootfs /bin/sleep 3600

# With environment variables
sudo python3 -m mini_docker run \
    -e MY_VAR=hello \
    -e DEBUG=1 \
    ./rootfs /bin/sh

# With volume mount
sudo python3 -m mini_docker run \
    -v /host/data:/container/data \
    ./rootfs /bin/sh

# Rootless mode
python3 -m mini_docker run --rootless ./rootfs /bin/sh

# Without OverlayFS (chroot only)
sudo python3 -m mini_docker run --no-overlay ./rootfs /bin/sh

# Auto-remove after exit
sudo python3 -m mini_docker run --rm ./rootfs /bin/echo "Temporary"

# Run inside a pod
sudo python3 -m mini_docker run --pod my-pod ./rootfs /bin/sh
```

---

### run-oci

Run a container from an OCI bundle.

```bash
python3 -m mini_docker run-oci [OPTIONS] BUNDLE_PATH
```

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--detach` | `-d` | false | Run in background |
| `--rootless` | | false | Run without root |

#### OCI Bundle Structure

An OCI bundle must contain:
- `rootfs/` - The container's root filesystem
- `config.json` - OCI runtime specification

#### Example config.json

```json
{
  "ociVersion": "1.0.0",
  "process": {
    "args": ["/bin/sh"],
    "env": ["PATH=/usr/bin:/bin"],
    "cwd": "/"
  },
  "root": {
    "path": "rootfs"
  },
  "hostname": "oci-container",
  "linux": {
    "namespaces": [
      {"type": "pid"},
      {"type": "mount"},
      {"type": "uts"},
      {"type": "ipc"}
    ]
  }
}
```

#### Examples

```bash
# Prepare OCI bundle
mkdir -p oci-bundle/rootfs
cp -a ./rootfs/* oci-bundle/rootfs/
# Create config.json as shown above

# Run OCI bundle
sudo python3 -m mini_docker run-oci ./oci-bundle

# Run detached
sudo python3 -m mini_docker run-oci -d ./oci-bundle

# Run rootless
python3 -m mini_docker run-oci --rootless ./oci-bundle
```

---

### exec

Execute a command in a running container.

```bash
python3 -m mini_docker exec [OPTIONS] CONTAINER COMMAND [ARGS...]
```

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--interactive` | `-i` | Keep STDIN open |
| `--tty` | `-t` | Allocate pseudo-TTY |
| `--env` | `-e` | Set environment variable |
| `--workdir` | `-w` | Working directory |
| `--user` | `-u` | User to run as |

#### Examples

```bash
# Execute command in container
sudo python3 -m mini_docker exec mycontainer /bin/ls -la

# Interactive shell
sudo python3 -m mini_docker exec -it mycontainer /bin/sh

# Run as different user
sudo python3 -m mini_docker exec -u nobody mycontainer /bin/whoami
```

---

### ps

List containers.

```bash
python3 -m mini_docker ps [OPTIONS]
```

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--all` | `-a` | Show all containers (including stopped) |
| `--quiet` | `-q` | Only show container IDs |
| `--format` | `-f` | Format output (table, json) |

#### Examples

```bash
# List running containers
sudo python3 -m mini_docker ps

# List all containers
sudo python3 -m mini_docker ps -a

# Only IDs
sudo python3 -m mini_docker ps -q

# JSON output
sudo python3 -m mini_docker ps --format json
```

#### Output

```
CONTAINER ID    NAME        STATUS      CREATED         COMMAND
a1b2c3d4e5f6    webserver   running     5 minutes ago   /bin/sh
f6e5d4c3b2a1    database    stopped     1 hour ago      /bin/sh
```

---

### stop

Stop a running container.

```bash
python3 -m mini_docker stop [OPTIONS] CONTAINER [CONTAINER...]
```

#### Options

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--time` | `-t` | 10 | Seconds to wait before killing |
| `--force` | `-f` | false | Force stop (SIGKILL) |

#### Examples

```bash
# Stop container gracefully
sudo python3 -m mini_docker stop mycontainer

# Stop with timeout
sudo python3 -m mini_docker stop -t 30 mycontainer

# Force stop
sudo python3 -m mini_docker stop -f mycontainer

# Stop multiple containers
sudo python3 -m mini_docker stop web db cache
```

---

### rm

Remove a container.

```bash
python3 -m mini_docker rm [OPTIONS] CONTAINER [CONTAINER...]
```

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--force` | `-f` | Force remove running container |
| `--volumes` | `-v` | Remove associated volumes |

#### Examples

```bash
# Remove stopped container
sudo python3 -m mini_docker rm mycontainer

# Force remove running container
sudo python3 -m mini_docker rm -f mycontainer

# Remove multiple
sudo python3 -m mini_docker rm web db cache

# Remove with volumes
sudo python3 -m mini_docker rm -v mycontainer
```

---

### logs

Fetch logs of a container.

```bash
python3 -m mini_docker logs [OPTIONS] CONTAINER
```

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--follow` | `-f` | Follow log output |
| `--tail` | `-n` | Number of lines to show |
| `--timestamps` | `-t` | Show timestamps |

#### Examples

```bash
# Show all logs
sudo python3 -m mini_docker logs mycontainer

# Follow logs
sudo python3 -m mini_docker logs -f mycontainer

# Last 100 lines
sudo python3 -m mini_docker logs -n 100 mycontainer

# With timestamps
sudo python3 -m mini_docker logs -t mycontainer
```

---

### inspect

Display detailed information on a container.

```bash
python3 -m mini_docker inspect [OPTIONS] CONTAINER [CONTAINER...]
```

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--format` | `-f` | Format output (json, yaml) |

#### Examples

```bash
# Inspect container
sudo python3 -m mini_docker inspect mycontainer

# JSON output
sudo python3 -m mini_docker inspect --format json mycontainer
```

---

## Image Commands

### build

Build an image from an Imagefile.

```bash
python3 -m mini_docker build [OPTIONS] PATH
```

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--tag` | `-t` | Name and tag (name:tag) |
| `--file` | `-f` | Path to Imagefile |
| `--no-cache` | | Don't use cache |

#### Examples

```bash
# Build with tag
sudo python3 -m mini_docker build -t myimage:latest .

# Custom Imagefile
sudo python3 -m mini_docker build -f Imagefile.dev -t myimage:dev .

# No cache
sudo python3 -m mini_docker build --no-cache -t myimage:latest .
```

#### Imagefile Syntax

```dockerfile
# Base rootfs
FROM ./rootfs

# Set environment variables
ENV MY_VAR=value

# Run commands during build
RUN echo "Hello" > /hello.txt
RUN mkdir -p /app

# Copy files
COPY ./local/file /container/path

# Set working directory
WORKDIR /app

# Set default command
CMD ["/bin/sh"]

# Set entrypoint
ENTRYPOINT ["/bin/sh", "-c"]
```

---

### images

List images.

```bash
python3 -m mini_docker images [OPTIONS]
```

#### Examples

```bash
sudo python3 -m mini_docker images
```

---

### rmi

Remove an image.

```bash
python3 -m mini_docker rmi [OPTIONS] IMAGE [IMAGE...]
```

#### Examples

```bash
sudo python3 -m mini_docker rmi myimage:latest
```

---

## Pod Commands

### pod create

Create a new pod.

```bash
python3 -m mini_docker pod create [OPTIONS] NAME
```

#### Options

| Option | Description |
|--------|-------------|
| `--hostname` | Pod hostname |
| `--net` | Enable networking |

#### Examples

```bash
# Create pod
sudo python3 -m mini_docker pod create mypod

# With networking
sudo python3 -m mini_docker pod create --net mypod
```

---

### pod add

Add a container to a pod.

```bash
python3 -m mini_docker pod add [OPTIONS] POD ROOTFS COMMAND [ARGS...]
```

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--name` | `-n` | Container name |

#### Examples

```bash
# Add container to pod
sudo python3 -m mini_docker pod add mypod --name app ./rootfs /bin/sh

# Add sidecar
sudo python3 -m mini_docker pod add mypod --name sidecar ./rootfs /bin/sleep 3600
```

---

### pod ls

List pods and their containers.

```bash
python3 -m mini_docker pod ls [OPTIONS] [POD]
```

#### Examples

```bash
# List all pods
sudo python3 -m mini_docker pod ls

# List specific pod
sudo python3 -m mini_docker pod ls mypod
```

---

### pod inspect

Display detailed information on a pod.

```bash
python3 -m mini_docker pod inspect [OPTIONS] POD
```

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--format` | `-f` | Format output (json, yaml) |

#### Examples

```bash
# Inspect pod
sudo python3 -m mini_docker pod inspect mypod

# JSON output
sudo python3 -m mini_docker pod inspect --format json mypod
```

---

### pod rm

Remove a pod.

```bash
python3 -m mini_docker pod rm [OPTIONS] POD [POD...]
```

#### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--force` | `-f` | Force remove |

#### Examples

```bash
sudo python3 -m mini_docker pod rm mypod
```

---

## System Commands

### info

Display system information.

```bash
python3 -m mini_docker info
```

#### Output

```
Mini-Docker Info
================
Version:        1.0.0
Kernel:         5.15.0-generic
Cgroups:        v2
Namespaces:     pid, uts, mnt, ipc, net, user, cgroup
Seccomp:        enabled
Capabilities:   enabled
Containers:     5 running, 10 total
Images:         3
```

---

### version

Show version information.

```bash
python3 -m mini_docker version
```

#### Output

```
Mini-Docker version 1.0.0
Python version 3.10.0
Linux kernel 5.15.0-generic
```

---

### cleanup

Clean up unused resources.

```bash
python3 -m mini_docker cleanup [OPTIONS]
```

#### Options

| Option | Description |
|--------|-------------|
| `--all` | Remove all unused data |
| `--containers` | Remove stopped containers |
| `--images` | Remove unused images |
| `--volumes` | Remove unused volumes |

#### Examples

```bash
# Clean stopped containers
sudo python3 -m mini_docker cleanup --containers

# Clean everything
sudo python3 -m mini_docker cleanup --all
```

---

## Root Commands (Require sudo)

> **⚠️ These commands require root privileges (`sudo`) to function properly.**

The following features require root access due to Linux kernel restrictions:

### Resource Limiting (Cgroups v2)

| Feature | Command | What It Does |
|---------|---------|--------------|
| CPU Limit | `sudo python3 -m mini_docker run --cpu 50 ./rootfs /bin/sh` | Limits CPU to 50% |
| Memory Limit | `sudo python3 -m mini_docker run --memory 100M ./rootfs /bin/sh` | Limits memory to 100MB |
| Process Limit | `sudo python3 -m mini_docker run --pids-limit 20 ./rootfs /bin/sh` | Limits max processes to 20 |
| Combined | `sudo python3 -m mini_docker run --cpu 50 --memory 100M --pids-limit 20 ./rootfs /bin/sh` | All limits together |

**Verify cgroups:**
```bash
# Check CPU limit
cat /sys/fs/cgroup/mini-docker/<id>/cpu.max

# Check memory limit
cat /sys/fs/cgroup/mini-docker/<id>/memory.max

# Check pids limit
cat /sys/fs/cgroup/mini-docker/<id>/pids.max
```

### Networking (Bridge Mode)

| Feature | Command | What It Does |
|---------|---------|--------------|
| Create Bridge | `sudo python3 -m mini_docker run --net ./rootfs /bin/sh` | Creates veth pair and bridge |
| Check Bridge | `ip link show mini-docker0` | Verify bridge interface |
| Check veth | `ip link \| grep veth` | Verify virtual ethernet pairs |

**Inside container with networking:**
```bash
ip addr show eth0       # View assigned IP
ping -c 1 10.0.0.1      # Ping the bridge gateway
```

### OverlayFS

| Feature | Command | What It Does |
|---------|---------|--------------|
| With Overlay | `sudo python3 -m mini_docker run ./rootfs /bin/sh` | Uses copy-on-write filesystem |
| Without Overlay | `sudo python3 -m mini_docker run --no-overlay ./rootfs /bin/sh` | Falls back to chroot |

**Verify OverlayFS:**
```bash
# Check overlay mount
mount | grep overlay

# Check upper layer for writes
ls /var/lib/mini-docker/overlays/<id>/upper/
```

### Security Features

| Feature | Command | What It Does |
|---------|---------|--------------|
| Seccomp | Enabled by default | Filters dangerous syscalls |
| Capabilities | Dropped by default | Reduces container privileges |

### Pod Management (Full Features)

| Feature | Command |
|---------|---------|
| Create pod | `sudo python3 -m mini_docker pod create my-pod` |
| Add to pod | `sudo python3 -m mini_docker pod add my-pod --name app ./rootfs /bin/sh` |
| Run in pod | `sudo python3 -m mini_docker run --pod my-pod ./rootfs /bin/sh` |
| List pods | `sudo python3 -m mini_docker pod ls` |
| Inspect pod | `sudo python3 -m mini_docker pod inspect my-pod` |
| Remove pod | `sudo python3 -m mini_docker pod rm my-pod` |

---

## Safe Commands (Rootless Mode)

> **✅ These commands work WITHOUT root privileges using the `--rootless` flag.**

Rootless mode uses user namespaces to run containers without `sudo`. Some features are limited or disabled for safety.

### Container Lifecycle

| Action | Command |
|--------|---------|
| Run (foreground) | `python3 -m mini_docker run --rootless ./rootfs /bin/echo "Hello"` |
| Run (background) | `python3 -m mini_docker run --rootless -d --name mycon ./rootfs /bin/sleep 60` |
| List running | `python3 -m mini_docker ps` |
| List all | `python3 -m mini_docker ps -a` |
| View logs | `python3 -m mini_docker logs <id>` |
| Stop | `python3 -m mini_docker stop <id>` |
| Remove | `python3 -m mini_docker rm <id>` |
| Inspect | `python3 -m mini_docker inspect <id>` |
| Run OCI bundle | `python3 -m mini_docker run-oci --rootless ./oci-bundle` |

### Image Operations

| Action | Command |
|--------|---------|
| Build image | `python3 -m mini_docker build -t my-app .` |
| List images | `python3 -m mini_docker images` |

### Pod Management (Metadata Only)

| Action | Command |
|--------|---------|
| Create pod | `python3 -m mini_docker pod create my-pod` |
| List pods | `python3 -m mini_docker pod ls` |
| Inspect pod | `python3 -m mini_docker pod inspect my-pod` |
| Remove pod | `python3 -m mini_docker pod rm my-pod` |
| Run in pod | `python3 -m mini_docker run --rootless --pod my-pod ./rootfs /bin/sh` |

### Storage Locations (Rootless)

| Path | Purpose |
|------|---------|
| `~/.local/share/mini-docker/containers/` | Container metadata |
| `~/.local/share/mini-docker/images/` | Built images |
| `~/.local/share/mini-docker/logs/` | Container logs |
| `~/.local/share/mini-docker/pods/` | Pod configurations |

### Limitations in Rootless Mode

| Feature | Status | Notes |
|---------|--------|-------|
| Networking | ❌ Disabled | Bridge networking requires root |
| Cgroups | ⚠️ Advisory | Resource limits may not be enforced |
| OverlayFS | ⚠️ Fallback | Falls back to chroot if unavailable |
| Seccomp | ✅ Available | Syscall filtering works |
| Capabilities | ✅ Available | Privilege reduction works |

### Example: Safe Demo Sequence

```bash
# 1. Build an image
echo -e "FROM ./rootfs\nCMD [\"/bin/echo\", \"Hello Safe World\"]" > Imagefile
python3 -m mini_docker build -t my-image .

# 2. List images
python3 -m mini_docker images

# 3. Run a container (rootless)
python3 -m mini_docker run --rootless --name demo ./rootfs /bin/echo "Hello"

# 4. List containers
python3 -m mini_docker ps -a

# 5. Clean up
python3 -m mini_docker rm demo
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
| 125 | Docker daemon error |
| 126 | Command cannot execute |
| 127 | Command not found |
| 130 | Interrupted (Ctrl+C) |
| 137 | Container killed (SIGKILL) |
| 143 | Container terminated (SIGTERM) |

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `MINI_DOCKER_DEBUG` | Enable debug mode |
| `MINI_DOCKER_HOST` | Container storage path |
| `MINI_DOCKER_LOG_LEVEL` | Log level (debug, info, warn, error) |

---

## File Locations (Root Mode)

| Path | Purpose |
|------|---------|
| `/var/lib/mini-docker/containers/` | Container metadata and configs |
| `/var/lib/mini-docker/overlays/` | OverlayFS layers (upper, work, merged) |
| `/var/lib/mini-docker/images/` | Built images and layers |
| `/var/lib/mini-docker/pods/` | Pod configurations |
| `/var/lib/mini-docker/logs/` | Container logs |
| `/sys/fs/cgroup/mini-docker/` | Container cgroups |

---

## See Also

- [Quick Start Guide](QUICKSTART.md)
- [Examples](EXAMPLES.md)
- [Troubleshooting](TROUBLESHOOTING.md)
