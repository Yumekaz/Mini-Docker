# 🚀 Quick Start Guide

Get Mini-Docker running in 5 minutes!

---

## Prerequisites

Before you begin, make sure you have:

### System Requirements

| Requirement | Minimum | Check Command |
|-------------|---------|---------------|
| **Linux Kernel** | 4.18+ | `uname -r` |
| **Python** | 3.7+ | `python3 --version` |
| **Root access** | Yes (or rootless mode) | `whoami` |

### Required Kernel Features

```bash
# Check if your kernel supports these features
grep CONFIG_NAMESPACES /boot/config-$(uname -r)
grep CONFIG_CGROUPS /boot/config-$(uname -r)
grep CONFIG_SECCOMP /boot/config-$(uname -r)
grep CONFIG_OVERLAY_FS /boot/config-$(uname -r)
```

---

## Installation

### Step 1: Clone the Repository

```bash
git clone https://github.com/Yumekaz/Mini-Docker.git
cd Mini-Docker
```

### Step 2: Run Setup Script

```bash
# This creates the minimal rootfs
sudo ./setup.sh
```

### Step 3: Verify Installation

```bash
# Check that everything is in place
python3 -m mini_docker --help
```

You should see the help menu with available commands.

---

## Your First Container

### Run an Interactive Shell

```bash
sudo python3 -m mini_docker run ./rootfs /bin/sh
```

### Inside the Container

Once inside, try these commands:

```bash
# Check your PID (should be 1!)
echo $$

# You're init process - the first process in the container
ps aux

# Change hostname (isolated from host)
hostname my-container
hostname

# Check the filesystem (container's own view)
ls /

# Create a file (won't affect host)
echo "Hello from container" > /hello.txt
cat /hello.txt

# Exit the container
exit
```

---

## What Just Happened?

When you ran that container, Mini-Docker:

1. **Created Namespaces** - Isolated your process from the host
   - PID namespace: You got PID 1
   - UTS namespace: You could change hostname
   - Mount namespace: Separate filesystem view

2. **Set Up Cgroups** - Limited resources (if specified)

3. **Applied Seccomp** - Filtered dangerous syscalls

4. **Dropped Capabilities** - Reduced privileges

5. **Set Up Filesystem** - Created isolated rootfs with OverlayFS

---

## Try More Features

### Run with Resource Limits

```bash
# Limit memory to 50MB
sudo python3 -m mini_docker run --memory 50M ./rootfs /bin/sh

# Inside container, try to use more memory
# It will be limited!
```

### Run with CPU Limits

```bash
# Limit CPU to 25%
sudo python3 -m mini_docker run --cpu 25 ./rootfs /bin/sh
```

### Run with PID Limits

```bash
# Limit to 10 processes (prevents fork bombs)
sudo python3 -m mini_docker run --pids 10 ./rootfs /bin/sh

# Inside container, try:
# for i in $(seq 1 20); do sleep 100 & done
# It will fail after 10 processes!
```

### Run with Custom Hostname

```bash
sudo python3 -m mini_docker run --hostname webserver ./rootfs /bin/sh

# Inside container:
hostname  # Shows "webserver"
```

---

## Combine Options

```bash
sudo python3 -m mini_docker run \
    --hostname demo \
    --memory 100M \
    --cpu 50 \
    --pids 20 \
    ./rootfs /bin/sh
```

---

## Rootless Mode (No sudo)

```bash
# Run without root privileges
python3 -m mini_docker run --rootless ./rootfs /bin/sh
```

Note: Rootless mode has some limitations (networking, etc.)

---

## Common Issues

### "Permission denied"

```bash
# Make sure you're running with sudo
sudo python3 -m mini_docker run ./rootfs /bin/sh
```

### "Cgroup not found"

```bash
# Check if cgroups v2 is mounted
mount | grep cgroup2

# If not, you may need to enable unified cgroups
```

### "Kernel too old"

```bash
# Check kernel version
uname -r

# Need kernel 4.18 or newer
```

---

## Next Steps

Now that you've run your first container, explore:

1. 📖 [Architecture Overview](ARCHITECTURE.md) - How it works
2. 🔒 [Security Model](SECURITY-MODEL.md) - Security layers
3. 📚 [CLI Commands Reference](CLI-COMMANDS.md) - All commands
4. 🔧 [Troubleshooting](TROUBLESHOOTING.md) - Common issues

---

## Quick Reference

```bash
# Basic run
sudo python3 -m mini_docker run ./rootfs /bin/sh

# With limits
sudo python3 -m mini_docker run --memory 100M --cpu 50 ./rootfs /bin/sh

# Named container
sudo python3 -m mini_docker run --name mycontainer ./rootfs /bin/sh

# List containers
sudo python3 -m mini_docker ps

# Stop container
sudo python3 -m mini_docker stop mycontainer

# Rootless
python3 -m mini_docker run --rootless ./rootfs /bin/sh
```

---

**Congratulations!** 🎉 You've successfully run your first container with Mini-Docker!
