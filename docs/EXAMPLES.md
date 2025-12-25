# 📚 Examples & Use Cases

Real-world examples demonstrating Mini-Docker capabilities.

---

## Table of Contents

- [Basic Examples](#basic-examples)
- [Resource Management](#resource-management)
- [Networking](#networking)
- [Security Features](#security-features)
- [Pod Examples](#pod-examples)
- [Image Building](#image-building)
- [Advanced Scenarios](#advanced-scenarios)

---

## Basic Examples

### Hello World

The simplest container example:

```bash
# Run a single command
sudo python3 -m mini_docker run ./rootfs /bin/echo "Hello from Mini-Docker!"

# Output: Hello from Mini-Docker!
```

### Interactive Shell

Start an interactive shell session:

```bash
sudo python3 -m mini_docker run ./rootfs /bin/sh

# Inside container:
$ echo $$        # Shows PID 1
$ hostname       # Shows container hostname
$ ps aux         # Only container processes visible
$ exit           # Exit container
```

### Named Container

Create a container with a specific name:

```bash
# Create named container
sudo python3 -m mini_docker run --name myapp ./rootfs /bin/sh

# List containers
sudo python3 -m mini_docker ps

# Stop by name
sudo python3 -m mini_docker stop myapp

# Remove by name
sudo python3 -m mini_docker rm myapp
```

### Custom Hostname

Set a custom hostname:

```bash
sudo python3 -m mini_docker run --hostname production-web ./rootfs /bin/sh

# Inside container:
$ hostname
# Output: production-web
```

### Background Container

Run container in detached mode:

```bash
# Start in background
sudo python3 -m mini_docker run -d --name background ./rootfs /bin/sleep 3600

# Check it's running
sudo python3 -m mini_docker ps

# Execute command in running container
sudo python3 -m mini_docker exec background /bin/echo "Hello"

# Stop it
sudo python3 -m mini_docker stop background
```

---

## Resource Management

### Memory Limits

Prevent containers from using too much memory:

```bash
# Limit to 50MB
sudo python3 -m mini_docker run --memory 50M ./rootfs /bin/sh

# Inside container, try to allocate memory:
$ dd if=/dev/zero of=/dev/null bs=100M count=1
# Will be killed if exceeds limit
```

### CPU Limits

Limit CPU usage:

```bash
# Limit to 25% of one CPU
sudo python3 -m mini_docker run --cpu 25 ./rootfs /bin/sh

# Inside container, run CPU-intensive task:
$ while true; do :; done &
# Will only use 25% CPU
```

### Process Limits (Fork Bomb Protection)

Prevent fork bombs:

```bash
# Limit to 10 processes
sudo python3 -m mini_docker run --pids 10 ./rootfs /bin/sh

# Inside container, try fork bomb:
$ :(){ :|:& };:
# Will fail after 10 processes - system protected!
```

### Combined Limits

Apply multiple limits:

```bash
sudo python3 -m mini_docker run \
    --name limited \
    --memory 100M \
    --cpu 50 \
    --pids 20 \
    ./rootfs /bin/sh
```

### Monitoring Resource Usage

Check container resource usage:

```bash
# In another terminal, check cgroup stats:
cat /sys/fs/cgroup/mini-docker/<container-id>/memory.current
cat /sys/fs/cgroup/mini-docker/<container-id>/cpu.stat
cat /sys/fs/cgroup/mini-docker/<container-id>/pids.current
```

---

## Networking

### Isolated Network Namespace

Container with its own network stack:

```bash
sudo python3 -m mini_docker run --net ./rootfs /bin/sh

# Inside container:
$ ip addr show
# Shows only container's interfaces

$ ip route
# Shows container's routing table
```

### Container-to-Host Communication

Containers can reach the host:

```bash
sudo python3 -m mini_docker run --net ./rootfs /bin/sh

# Inside container:
$ ping -c 1 10.0.0.1  # Host bridge IP
```

### Internet Access (via NAT)

Container can access internet:

```bash
sudo python3 -m mini_docker run --net ./rootfs /bin/sh

# Inside container:
$ ping -c 1 8.8.8.8
# Should work if host has internet
```

### Port Forwarding Example

Manual port forwarding (on host):

```bash
# Start container with networking
sudo python3 -m mini_docker run --net --name web ./rootfs /bin/sh

# On host, forward port:
sudo iptables -t nat -A PREROUTING -p tcp --dport 8080 \
    -j DNAT --to-destination 10.0.0.2:80
```

---

## Security Features

### Demonstrating Process Isolation

```bash
# Terminal 1: Start container
sudo python3 -m mini_docker run --name isolated ./rootfs /bin/sh

# Inside container:
$ ps aux
# Only sees container processes

# Terminal 2: On host
ps aux | grep sleep
# Host sees all processes, container doesn't
```

### Demonstrating Filesystem Isolation

```bash
sudo python3 -m mini_docker run ./rootfs /bin/sh

# Inside container:
$ echo "secret" > /container-file.txt
$ cat /container-file.txt
$ exit

# On host:
$ cat /container-file.txt
# File doesn't exist on host - isolated!
```

### Demonstrating Hostname Isolation

```bash
sudo python3 -m mini_docker run --hostname container1 ./rootfs /bin/sh

# Inside container:
$ hostname container-hacked
$ hostname
# Shows: container-hacked

# On host:
$ hostname
# Unchanged - isolated!
```

### Seccomp in Action

```bash
sudo python3 -m mini_docker run ./rootfs /bin/sh

# Inside container, try blocked syscall:
$ mount -t proc proc /proc
# Operation not permitted - seccomp blocked it!
```

### Capability Restrictions

```bash
sudo python3 -m mini_docker run ./rootfs /bin/sh

# Inside container:
$ cat /proc/self/status | grep Cap
# Shows reduced capabilities
```

### Rootless Container

Run without root privileges:

```bash
# No sudo needed!
python3 -m mini_docker run --rootless ./rootfs /bin/sh

# Inside container:
$ whoami
$ id
```

---

## Pod Examples

### Basic Pod

Create a pod with multiple containers sharing namespaces:

```bash
# Create pod
sudo python3 -m mini_docker pod create mypod

# Add main application
sudo python3 -m mini_docker pod add mypod --name app ./rootfs /bin/sh

# Add sidecar (shares network with app)
sudo python3 -m mini_docker pod add mypod --name sidecar ./rootfs /bin/sleep 3600

# List pod
sudo python3 -m mini_docker pod ps mypod

# Cleanup
sudo python3 -m mini_docker pod rm mypod
```

### Pod with Shared Network

Containers in a pod share the same network namespace:

```bash
# Create pod with networking
sudo python3 -m mini_docker pod create --net webpod

# Add web server container
sudo python3 -m mini_docker pod add webpod --name nginx ./rootfs /bin/sh

# Add log collector (can access localhost:80)
sudo python3 -m mini_docker pod add webpod --name logs ./rootfs /bin/sh

# Both containers see same localhost
```

---

## Image Building

### Simple Imagefile

Create `Imagefile`:

```dockerfile
FROM ./rootfs

RUN echo "Hello" > /hello.txt
RUN mkdir -p /app

CMD ["/bin/sh"]
```

Build and run:

```bash
# Build
sudo python3 -m mini_docker build -t myimage:v1 .

# Run
sudo python3 -m mini_docker run myimage:v1 /bin/sh
```

### Application Image

Create `Imagefile.app`:

```dockerfile
FROM ./rootfs

# Set environment
ENV APP_ENV=production
ENV DEBUG=false

# Create app directory
RUN mkdir -p /app/data

# Copy application
COPY ./myapp.sh /app/myapp.sh
RUN chmod +x /app/myapp.sh

# Set working directory
WORKDIR /app

# Default command
CMD ["/app/myapp.sh"]
```

Build:

```bash
sudo python3 -m mini_docker build -f Imagefile.app -t myapp:latest .
```

---

## Advanced Scenarios

### Development Environment

```bash
# Mount source code from host
sudo python3 -m mini_docker run \
    --name dev \
    -v $(pwd)/src:/app/src \
    -e DEBUG=1 \
    -w /app \
    ./rootfs /bin/sh
```

### Testing in Isolation

```bash
#!/bin/bash
# test-in-container.sh

# Run tests in isolated environment
sudo python3 -m mini_docker run \
    --rm \
    --memory 500M \
    --cpu 100 \
    -v $(pwd):/tests \
    ./rootfs /bin/sh -c "cd /tests && ./run_tests.sh"

echo "Tests completed in container"
```

### Batch Processing

```bash
#!/bin/bash
# Process files in parallel containers

for file in /data/*.txt; do
    sudo python3 -m mini_docker run \
        --rm \
        --memory 100M \
        -v /data:/data \
        ./rootfs /bin/sh -c "process $file" &
done

wait
echo "All processing complete"
```

### Resource Stress Testing

```bash
#!/bin/bash
# Test resource limits

echo "Testing memory limit..."
sudo python3 -m mini_docker run --rm --memory 50M ./rootfs /bin/sh -c '
    # Try to allocate 100MB
    dd if=/dev/zero of=/tmp/test bs=1M count=100 2>&1 || echo "Memory limit worked!"
'

echo "Testing CPU limit..."
sudo python3 -m mini_docker run --rm --cpu 10 ./rootfs /bin/sh -c '
    # CPU intensive task - will be throttled
    time $(for i in $(seq 1 1000000); do :; done)
'

echo "Testing PID limit..."
sudo python3 -m mini_docker run --rm --pids 5 ./rootfs /bin/sh -c '
    # Try to create many processes
    for i in $(seq 1 10); do sleep 100 & done 2>&1 || echo "PID limit worked!"
'
```

### Container Orchestration Script

```bash
#!/bin/bash
# Simple orchestration example

# Start database
sudo python3 -m mini_docker run -d --name db --memory 256M ./rootfs /bin/sh

# Wait for db
sleep 2

# Start app connected to db
sudo python3 -m mini_docker run -d --name app --memory 128M ./rootfs /bin/sh

# Start worker
sudo python3 -m mini_docker run -d --name worker --memory 128M ./rootfs /bin/sh

echo "Stack started:"
sudo python3 -m mini_docker ps

# Cleanup function
cleanup() {
    sudo python3 -m mini_docker stop db app worker
    sudo python3 -m mini_docker rm db app worker
}

trap cleanup EXIT
```

---

## Quick Reference

```bash
# Container lifecycle
sudo python3 -m mini_docker run ./rootfs /bin/sh      # Run
sudo python3 -m mini_docker ps                         # List
sudo python3 -m mini_docker stop <name>                # Stop
sudo python3 -m mini_docker rm <name>                  # Remove

# Resource limits
--memory 100M    # Memory limit
--cpu 50         # CPU percentage
--pids 20        # Process limit

# Networking
--net            # Enable networking

# Pods
sudo python3 -m mini_docker pod create mypod
sudo python3 -m mini_docker pod add mypod --name app ./rootfs /bin/sh

# Building
sudo python3 -m mini_docker build -t name:tag .
```

---

## See Also

- [CLI Commands Reference](CLI-COMMANDS.md)
- [Troubleshooting](TROUBLESHOOTING.md)
- [Architecture](ARCHITECTURE.md)
