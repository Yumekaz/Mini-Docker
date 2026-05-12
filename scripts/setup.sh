#!/bin/bash
# Mini-Docker Setup Script
# Run as root or with sudo

set -e

echo "=== Mini-Docker Setup Script ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo ./setup.sh)"
    exit 1
fi

# Check for Linux
if [ "$(uname)" != "Linux" ]; then
    echo "Mini-Docker only runs on Linux"
    exit 1
fi

echo "[1/7] Installing system dependencies..."
apt-get update
apt-get install -y \
    python3 \
    python3-pip \
    iproute2 \
    iptables \
    bridge-utils \
    debootstrap \
    util-linux \
    cgroup-tools

echo "[2/7] Enabling cgroups v2..."
# Check if cgroups v2 is mounted
if ! mount | grep -q "cgroup2"; then
    echo "Mounting cgroups v2..."
    mount -t cgroup2 none /sys/fs/cgroup 2>/dev/null || true
fi

# Enable controllers
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
    echo "+cpu +memory +io +pids" > /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || true
fi

echo "[3/7] Creating Mini-Docker directories..."
mkdir -p /var/lib/mini-docker/containers
mkdir -p /var/lib/mini-docker/images
mkdir -p /var/lib/mini-docker/overlay
mkdir -p /var/lib/mini-docker/pods
mkdir -p /var/run/mini-docker

echo "[4/7] Setting up network bridge..."
if ! ip link show mini-docker0 &>/dev/null; then
    ip link add name mini-docker0 type bridge
    ip addr add 10.0.0.1/24 dev mini-docker0
    ip link set mini-docker0 up
fi

echo "[5/7] Setting up NAT..."
iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -j MASQUERADE 2>/dev/null || true
echo 1 > /proc/sys/net/ipv4/ip_forward

echo "[6/7] Creating minimal rootfs..."
if [ ! -d "./rootfs/bin" ]; then
    mkdir -p rootfs/{bin,proc,sys,dev,etc,tmp,var,home,root}
    
    # Try to download alpine minirootfs
    if command -v wget &>/dev/null; then
        echo "Downloading Alpine Linux minirootfs..."
        wget -q https://dl-cdn.alpinelinux.org/alpine/v3.18/releases/x86_64/alpine-minirootfs-3.18.4-x86_64.tar.gz -O /tmp/alpine.tar.gz
        tar -xzf /tmp/alpine.tar.gz -C rootfs
        rm /tmp/alpine.tar.gz
    else
        echo "wget not found, creating minimal busybox rootfs..."
        # Fallback: copy busybox
        if command -v busybox &>/dev/null; then
            cp $(which busybox) rootfs/bin/
            cd rootfs/bin
            for cmd in sh ls cat echo mkdir rm cp mv ps mount umount sleep; do
                ln -sf busybox $cmd
            done
            cd -
        fi
    fi
    
    # Create basic files
    echo "root:x:0:0:root:/root:/bin/sh" > rootfs/etc/passwd
    echo "root:x:0:" > rootfs/etc/group
    echo "nameserver 8.8.8.8" > rootfs/etc/resolv.conf
    echo "127.0.0.1 localhost" > rootfs/etc/hosts
fi

echo "[7/7] Setting permissions..."
chmod +x run.sh 2>/dev/null || true
chmod -R 755 mini_docker/

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Usage examples:"
echo "  sudo python3 -m mini_docker run ./rootfs /bin/sh"
echo "  sudo python3 -m mini_docker ps"
echo "  sudo python3 -m mini_docker logs <container-id>"
echo ""
echo "For rootless mode, see README.md"
