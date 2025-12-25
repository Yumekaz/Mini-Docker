# 🔧 Troubleshooting Guide

Solutions to common issues with Mini-Docker.

---

## Table of Contents

- [Installation Issues](#installation-issues)
- [Permission Errors](#permission-errors)
- [Namespace Errors](#namespace-errors)
- [Cgroup Errors](#cgroup-errors)
- [Filesystem Errors](#filesystem-errors)
- [Network Errors](#network-errors)
- [Seccomp Errors](#seccomp-errors)
- [Resource Limit Issues](#resource-limit-issues)
- [Container Lifecycle Issues](#container-lifecycle-issues)
- [Performance Issues](#performance-issues)
- [Debugging Tips](#debugging-tips)

---

## Installation Issues

### Python Version Too Old

**Error:**
```
SyntaxError: invalid syntax
```

**Cause:** Python version is below 3.7.

**Solution:**
```bash
# Check Python version
python3 --version

# Install Python 3.7+ if needed
sudo apt update
sudo apt install python3.10

# Or use pyenv
pyenv install 3.10.0
pyenv global 3.10.0
```

### Module Not Found

**Error:**
```
ModuleNotFoundError: No module named 'mini_docker'
```

**Solution:**
```bash
# Make sure you're in the project directory
cd Mini-Docker

# Install in development mode
pip install -e .

# Or run as module
python3 -m mini_docker run ./rootfs /bin/sh
```

### Setup Script Fails

**Error:**
```
./setup.sh: Permission denied
```

**Solution:**
```bash
# Make executable
chmod +x setup.sh

# Run with sudo
sudo ./setup.sh
```

---

## Permission Errors

### Operation Not Permitted

**Error:**
```
OSError: [Errno 1] Operation not permitted
```

**Cause:** Not running as root (and not using rootless mode).

**Solution:**
```bash
# Run with sudo
sudo python3 -m mini_docker run ./rootfs /bin/sh

# OR use rootless mode
python3 -m mini_docker run --rootless ./rootfs /bin/sh
```

### Cannot Create Namespace

**Error:**
```
PermissionError: [Errno 1] Operation not permitted: 'clone'
```

**Cause:** Insufficient privileges for namespace creation.

**Solution:**
```bash
# Check if user namespaces are enabled
cat /proc/sys/kernel/unprivileged_userns_clone

# If 0, enable them (as root):
sudo sysctl -w kernel.unprivileged_userns_clone=1

# Or run with sudo
sudo python3 -m mini_docker run ./rootfs /bin/sh
```

### Cannot Write to Cgroup

**Error:**
```
PermissionError: [Errno 13] Permission denied: '/sys/fs/cgroup/...'
```

**Solution:**
```bash
# Must run as root for cgroup access
sudo python3 -m mini_docker run ./rootfs /bin/sh

# Check cgroup permissions
ls -la /sys/fs/cgroup/
```

---

## Namespace Errors

### Clone Failed

**Error:**
```
OSError: [Errno 22] Invalid argument: clone
```

**Cause:** Invalid namespace flags or kernel doesn't support requested namespace.

**Solution:**
```bash
# Check kernel version
uname -r

# Minimum: 4.18 for all namespace types
# Check available namespaces:
ls /proc/self/ns/
```

### User Namespace Not Available

**Error:**
```
OSError: User namespace not supported
```

**Solution:**
```bash
# Check if enabled
cat /proc/sys/kernel/unprivileged_userns_clone

# Enable it
sudo sysctl -w kernel.unprivileged_userns_clone=1

# Make permanent
echo "kernel.unprivileged_userns_clone=1" | sudo tee /etc/sysctl.d/99-userns.conf
```

### PID Namespace Issue

**Error:**
```
Container process shows PID other than 1
```

**Cause:** PID namespace not properly set up.

**Solution:**
```bash
# Inside container, check:
echo $$  # Should be 1

# If not 1, check if clone used CLONE_NEWPID
# Verify in code that CLONE_NEWPID is set
```

---

## Cgroup Errors

### Cgroup v2 Not Available

**Error:**
```
FileNotFoundError: /sys/fs/cgroup/cgroup.controllers
```

**Cause:** System using cgroups v1 or cgroups not mounted.

**Solution:**
```bash
# Check cgroup version
mount | grep cgroup

# For cgroups v2, you should see:
# cgroup2 on /sys/fs/cgroup type cgroup2

# If using v1, boot with unified cgroups:
# Add to kernel boot parameters: systemd.unified_cgroup_hierarchy=1

# GRUB: Edit /etc/default/grub
GRUB_CMDLINE_LINUX="systemd.unified_cgroup_hierarchy=1"
sudo update-grub
sudo reboot
```

### Cgroup Controller Not Available

**Error:**
```
Controller 'memory' not available
```

**Solution:**
```bash
# Check available controllers
cat /sys/fs/cgroup/cgroup.controllers

# Enable controller if needed (as root):
echo "+memory +cpu +pids" > /sys/fs/cgroup/cgroup.subtree_control
```

### Cannot Create Cgroup

**Error:**
```
OSError: [Errno 2] No such file or directory: '/sys/fs/cgroup/mini-docker'
```

**Solution:**
```bash
# Create the cgroup directory
sudo mkdir -p /sys/fs/cgroup/mini-docker

# Set proper permissions
sudo chown -R root:root /sys/fs/cgroup/mini-docker
```

### Memory Limit Not Working

**Error:**
Container uses more memory than specified.

**Solution:**
```bash
# Verify memory controller is enabled
cat /sys/fs/cgroup/mini-docker/<container>/memory.max

# Check if value was set correctly
# Should show limit in bytes

# Verify container is in cgroup
cat /sys/fs/cgroup/mini-docker/<container>/cgroup.procs
```

---

## Filesystem Errors

### OverlayFS Mount Failed

**Error:**
```
mount: unknown filesystem type 'overlay'
```

**Solution:**
```bash
# Check if overlayfs module is loaded
lsmod | grep overlay

# Load it
sudo modprobe overlay

# Make permanent
echo "overlay" | sudo tee /etc/modules-load.d/overlay.conf
```

### Pivot Root Failed

**Error:**
```
OSError: [Errno 22] Invalid argument: pivot_root
```

**Cause:** pivot_root has specific requirements.

**Solution:**
```bash
# Ensure new_root is a mount point
sudo mount --bind /path/to/rootfs /path/to/rootfs

# Ensure old_root is under new_root
mkdir -p /path/to/rootfs/oldroot

# pivot_root new_root new_root/oldroot
```

### Rootfs Not Found

**Error:**
```
FileNotFoundError: ./rootfs not found
```

**Solution:**
```bash
# Run setup script
sudo ./setup.sh

# Or manually create rootfs
./scripts/create_rootfs.sh

# Verify rootfs exists
ls -la ./rootfs/
```

### Cannot Execute Binary

**Error:**
```
OSError: [Errno 8] Exec format error
```

**Cause:** Binary architecture mismatch or missing interpreter.

**Solution:**
```bash
# Check binary type
file ./rootfs/bin/sh

# Should match your system architecture
uname -m

# For busybox, ensure it's statically linked:
file ./rootfs/bin/busybox
# Should show: statically linked
```

---

## Network Errors

### Cannot Create veth Pair

**Error:**
```
OSError: [Errno 1] Operation not permitted: 'ip link add'
```

**Solution:**
```bash
# Must run as root for network setup
sudo python3 -m mini_docker run --net ./rootfs /bin/sh

# Check if iproute2 is installed
which ip
sudo apt install iproute2
```

### Bridge Not Found

**Error:**
```
Cannot find bridge mini-docker-br0
```

**Solution:**
```bash
# Create bridge manually
sudo ip link add name mini-docker-br0 type bridge
sudo ip link set mini-docker-br0 up
sudo ip addr add 10.0.0.1/24 dev mini-docker-br0
```

### No Internet Access

**Error:**
Container can't reach external networks.

**Solution:**
```bash
# Enable IP forwarding
sudo sysctl -w net.ipv4.ip_forward=1

# Set up NAT
sudo iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -o eth0 -j MASQUERADE

# Check firewall rules
sudo iptables -L -n
sudo iptables -t nat -L -n
```

### DNS Not Working

**Error:**
Container can't resolve hostnames.

**Solution:**
```bash
# Inside container, create resolv.conf
echo "nameserver 8.8.8.8" > /etc/resolv.conf

# Or copy from host
cp /etc/resolv.conf ./rootfs/etc/resolv.conf
```

---

## Seccomp Errors

### Seccomp Not Available

**Error:**
```
OSError: Seccomp not supported
```

**Solution:**
```bash
# Check kernel config
grep CONFIG_SECCOMP /boot/config-$(uname -r)

# Should show:
# CONFIG_SECCOMP=y
# CONFIG_SECCOMP_FILTER=y
```

### Syscall Blocked

**Error:**
```
Operation not permitted (inside container)
```

**Cause:** Syscall blocked by seccomp filter.

**Solution:**
```bash
# This is expected behavior for dangerous syscalls
# If you need a specific syscall, modify seccomp.py whitelist
# (Not recommended for security reasons)

# Debug: run without seccomp (development only!)
# Modify container.py to skip seccomp setup
```

### BPF Program Load Failed

**Error:**
```
OSError: BPF program load failed
```

**Solution:**
```bash
# Check kernel version (need 3.17+)
uname -r

# Check BPF support
grep CONFIG_BPF /boot/config-$(uname -r)
```

---

## Resource Limit Issues

### Memory Limit Ignored

**Solution:**
```bash
# Verify memory controller
cat /sys/fs/cgroup/cgroup.controllers | grep memory

# Check memory.max
cat /sys/fs/cgroup/mini-docker/<id>/memory.max

# Check if container joined cgroup
cat /sys/fs/cgroup/mini-docker/<id>/cgroup.procs
```

### CPU Limit Not Working

**Solution:**
```bash
# Verify cpu controller
cat /sys/fs/cgroup/cgroup.controllers | grep cpu

# Check cpu.max (format: quota period)
cat /sys/fs/cgroup/mini-docker/<id>/cpu.max
# Example: 50000 100000 = 50% CPU
```

### PID Limit Not Working

**Solution:**
```bash
# Verify pids controller
cat /sys/fs/cgroup/cgroup.controllers | grep pids

# Check pids.max
cat /sys/fs/cgroup/mini-docker/<id>/pids.max
```

---

## Container Lifecycle Issues

### Container Won't Start

**Debug Steps:**
```bash
# Enable debug mode
sudo python3 -m mini_docker --debug run ./rootfs /bin/sh

# Check for errors in each step:
# 1. Namespace creation
# 2. Cgroup setup
# 3. Filesystem setup
# 4. Seccomp filter
# 5. execve()
```

### Container Exits Immediately

**Cause:** Command exits or fails.

**Solution:**
```bash
# Check command exists in rootfs
ls -la ./rootfs/bin/sh

# Try different command
sudo python3 -m mini_docker run ./rootfs /bin/busybox sh

# Check exit code
echo $?
```

### Cannot Stop Container

**Solution:**
```bash
# Find container process
ps aux | grep mini-docker

# Force kill
sudo kill -9 <pid>

# Clean up cgroup
sudo rmdir /sys/fs/cgroup/mini-docker/<id>
```

### Zombie Processes

**Cause:** Container init not reaping children.

**Solution:**
```bash
# The container's PID 1 should handle SIGCHLD
# Mini-Docker should set up proper signal handling

# Manual cleanup:
sudo kill -9 $(pgrep -P <container-pid>)
```

---

## Performance Issues

### Slow Container Startup

**Solutions:**
```bash
# Use pre-created rootfs
# Avoid large file copies

# Pre-create overlay directories
mkdir -p /tmp/mini-docker/{upper,work,merged}
```

### High CPU Usage

**Debug:**
```bash
# Check what's using CPU
top -p $(pgrep -f mini_docker)

# Profile Python code
python3 -m cProfile -m mini_docker run ./rootfs /bin/sh
```

### Memory Leaks

**Debug:**
```bash
# Monitor memory
watch -n 1 'ps aux | grep mini_docker'

# Check for uncleaned resources
ls /sys/fs/cgroup/mini-docker/
```

---

## Debugging Tips

### Enable Debug Output

```bash
sudo python3 -m mini_docker --debug run ./rootfs /bin/sh
```

### Enable Debug Mode

```bash
export MINI_DOCKER_DEBUG=1
sudo python3 -m mini_docker run ./rootfs /bin/sh
```

### Check System Logs

```bash
# Check dmesg for kernel errors
sudo dmesg | tail -50

# Check syslog
sudo tail -f /var/log/syslog
```

### Inspect Container State

```bash
# List processes in container's cgroup
cat /sys/fs/cgroup/mini-docker/<id>/cgroup.procs

# Check container's namespaces
ls -la /proc/<pid>/ns/

# Check mount namespace
cat /proc/<pid>/mounts
```

### Manual Testing

```bash
# Test namespace creation
sudo unshare --pid --fork --mount-proc /bin/sh

# Test cgroup
sudo mkdir /sys/fs/cgroup/test
echo $$ | sudo tee /sys/fs/cgroup/test/cgroup.procs

# Test overlayfs
sudo mount -t overlay overlay \
    -o lowerdir=/lower,upperdir=/upper,workdir=/work \
    /merged
```

### Strace for Debugging

```bash
# Trace syscalls
sudo strace -f python3 -m mini_docker run ./rootfs /bin/sh 2>&1 | head -100
```

---

## Getting Help

If you're still stuck:

1. **Check existing issues:** [GitHub Issues](https://github.com/Yumekaz/Mini-Docker/issues)
2. **Search documentation:** [Docs](../docs/)
3. **Enable debug mode** and capture output
4. **Open new issue** with:
   - Mini-Docker version
   - Linux kernel version
   - Python version
   - Complete error message
   - Steps to reproduce

---

## See Also

- [Architecture](ARCHITECTURE.md)
- [Security Model](SECURITY-MODEL.md)
- [CLI Commands](CLI-COMMANDS.md)
