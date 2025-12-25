#!/usr/bin/env python3
"""
Container Networking for Mini-Docker.

Implements container networking with:
- veth pairs: Virtual Ethernet device pairs
- Linux bridge: Software network bridge
- IP assignment: Private IP addresses
- NAT: Network Address Translation for outbound traffic

Network Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                        HOST                                  │
    │                                                              │
    │  ┌──────────────┐      ┌──────────────┐                     │
    │  │ Container 1  │      │ Container 2  │                     │
    │  │ eth0         │      │ eth0         │                     │
    │  │ 10.0.0.2     │      │ 10.0.0.3     │                     │
    │  └──────┬───────┘      └──────┬───────┘                     │
    │         │                      │                             │
    │     veth1-host             veth2-host                        │
    │         │                      │                             │
    │  ┌──────┴──────────────────────┴──────┐                     │
    │  │         mini-docker0 (bridge)       │                     │
    │  │              10.0.0.1               │                     │
    │  └─────────────────┬──────────────────┘                     │
    │                    │                                         │
    │                   NAT                                        │
    │                    │                                         │
    │  ┌─────────────────┴──────────────────┐                     │
    │  │           eth0 (host)               │                     │
    │  └────────────────────────────────────┘                     │
    │                                                              │
    └─────────────────────────────────────────────────────────────┘
"""

import os
import subprocess
from typing import Optional, Tuple

from mini_docker.utils import generate_mac_address, get_available_ip

# Network configuration
BRIDGE_NAME = "mini-docker0"
BRIDGE_IP = "10.0.0.1"
BRIDGE_SUBNET = "10.0.0.0/24"
BRIDGE_NETMASK = "255.255.255.0"


class NetworkError(Exception):
    """Exception raised for network operations."""

    pass


def run_ip_command(args: list, check: bool = True) -> subprocess.CompletedProcess:
    """
    Run an 'ip' command.

    Args:
        args: Arguments to pass to 'ip'
        check: Whether to raise on non-zero exit

    Returns:
        CompletedProcess instance
    """
    cmd = ["ip"] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def run_iptables_command(args: list, check: bool = True) -> subprocess.CompletedProcess:
    """
    Run an 'iptables' command.

    Args:
        args: Arguments to pass to 'iptables'
        check: Whether to raise on non-zero exit

    Returns:
        CompletedProcess instance
    """
    cmd = ["iptables"] + args
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def bridge_exists(name: str = BRIDGE_NAME) -> bool:
    """Check if bridge device exists."""
    result = run_ip_command(["link", "show", name], check=False)
    return result.returncode == 0


def create_bridge(name: str = BRIDGE_NAME, ip: str = BRIDGE_IP) -> None:
    """
    Create a network bridge.

    A bridge acts as a virtual switch, connecting multiple
    network interfaces together.

    Args:
        name: Bridge device name
        ip: IP address to assign to bridge
    """
    if bridge_exists(name):
        return

    try:
        # Create bridge device
        run_ip_command(["link", "add", "name", name, "type", "bridge"])

        # Assign IP address
        run_ip_command(["addr", "add", f"{ip}/24", "dev", name])

        # Bring bridge up
        run_ip_command(["link", "set", name, "up"])

    except subprocess.CalledProcessError as e:
        raise NetworkError(f"Failed to create bridge: {e}")


def delete_bridge(name: str = BRIDGE_NAME) -> None:
    """Delete a network bridge."""
    if not bridge_exists(name):
        return

    try:
        run_ip_command(["link", "set", name, "down"])
        run_ip_command(["link", "delete", name, "type", "bridge"])
    except subprocess.CalledProcessError:
        pass


def create_veth_pair(veth_host: str, veth_container: str) -> None:
    """
    Create a veth (virtual ethernet) pair.

    Veth pairs act like a virtual cable connecting two endpoints.
    One end stays on the host, the other moves to the container.

    Args:
        veth_host: Name for host-side veth
        veth_container: Name for container-side veth
    """
    try:
        run_ip_command(
            ["link", "add", veth_host, "type", "veth", "peer", "name", veth_container]
        )
    except subprocess.CalledProcessError as e:
        raise NetworkError(f"Failed to create veth pair: {e}")


def delete_veth(veth_name: str) -> None:
    """Delete a veth interface (automatically deletes peer)."""
    try:
        run_ip_command(["link", "delete", veth_name], check=False)
    except subprocess.CalledProcessError:
        pass


def move_veth_to_netns(veth_name: str, pid: int) -> None:
    """
    Move veth interface to a network namespace.

    This is done by specifying the PID of a process in the target
    network namespace.

    Args:
        veth_name: Veth interface to move
        pid: PID of process in target namespace
    """
    try:
        run_ip_command(["link", "set", veth_name, "netns", str(pid)])
    except subprocess.CalledProcessError as e:
        raise NetworkError(f"Failed to move veth to netns: {e}")


def attach_to_bridge(veth_name: str, bridge: str = BRIDGE_NAME) -> None:
    """
    Attach a veth interface to a bridge.

    Args:
        veth_name: Veth interface to attach
        bridge: Bridge device name
    """
    try:
        run_ip_command(["link", "set", veth_name, "master", bridge])
        run_ip_command(["link", "set", veth_name, "up"])
    except subprocess.CalledProcessError as e:
        raise NetworkError(f"Failed to attach to bridge: {e}")


def setup_nat(subnet: str = BRIDGE_SUBNET) -> None:
    """
    Set up NAT (Network Address Translation) for containers.

    This allows containers to access the internet through the host.

    Args:
        subnet: Source subnet to NAT
    """
    # Enable IP forwarding
    try:
        with open("/proc/sys/net/ipv4/ip_forward", "w") as f:
            f.write("1")
    except (IOError, PermissionError):
        pass

    # Add NAT rule (MASQUERADE)
    try:
        run_iptables_command(
            ["-t", "nat", "-C", "POSTROUTING", "-s", subnet, "-j", "MASQUERADE"],
            check=False,
        )
    except subprocess.CalledProcessError:
        # Rule doesn't exist, add it
        try:
            run_iptables_command(
                ["-t", "nat", "-A", "POSTROUTING", "-s", subnet, "-j", "MASQUERADE"]
            )
        except subprocess.CalledProcessError:
            pass


def setup_container_networking(
    container_id: str, container_pid: int, ip_address: Optional[str] = None
) -> Tuple[str, str, str]:
    """
    Set up complete networking for a container.

    This function:
    1. Creates a bridge (if not exists)
    2. Creates a veth pair
    3. Attaches host veth to bridge
    4. Moves container veth to container's network namespace
    5. Sets up NAT

    Args:
        container_id: Container ID (used for interface naming)
        container_pid: PID of container init process
        ip_address: IP to assign (auto-assigned if None)

    Returns:
        Tuple of (veth_host, veth_container, ip_address)
    """
    # Create bridge if needed
    create_bridge()

    # Get IP address
    if ip_address is None:
        ip_address = get_available_ip()

    # Create veth pair names using container ID
    short_id = container_id[:8]
    veth_host = f"veth{short_id}"
    veth_container = f"eth0"

    # Truncate names to fit Linux limits (15 chars)
    veth_host = veth_host[:15]

    # Create veth pair
    create_veth_pair(veth_host, veth_container)

    # Attach host side to bridge
    attach_to_bridge(veth_host)

    # Move container side to container's network namespace
    move_veth_to_netns(veth_container, container_pid)

    # Set up NAT
    setup_nat()

    return veth_host, veth_container, ip_address


def configure_container_network(ip_address: str, gateway: str = BRIDGE_IP) -> None:
    """
    Configure networking inside the container.

    This should be called from within the container's network namespace.

    Args:
        ip_address: IP address for the container
        gateway: Default gateway (bridge IP)
    """
    try:
        # Bring up loopback
        run_ip_command(["link", "set", "lo", "up"])

        # Configure eth0
        run_ip_command(["addr", "add", f"{ip_address}/24", "dev", "eth0"])
        run_ip_command(["link", "set", "eth0", "up"])

        # Add default route
        run_ip_command(["route", "add", "default", "via", gateway])

    except subprocess.CalledProcessError as e:
        raise NetworkError(f"Failed to configure container network: {e}")


def cleanup_container_networking(container_id: str) -> None:
    """
    Clean up networking resources for a container.

    Args:
        container_id: Container ID
    """
    short_id = container_id[:8]
    veth_host = f"veth{short_id}"[:15]
    delete_veth(veth_host)
    # Try alternate naming patterns
    delete_veth(f"veth-{short_id}"[:15])


class Network:
    """
    Network manager for a container.

    Example:
        net = Network(container_id)
        veth_host, veth_container, ip = net.setup(container_pid)
        # Inside container:
        net.configure_inside(ip)
    """

    def __init__(self, container_id: str):
        self.container_id = container_id
        self.veth_host = None
        self.veth_container = None
        self.ip_address = None
        self.mac_address = generate_mac_address()
        self._setup_called = False

    def setup(
        self, container_pid: int, ip: Optional[str] = None
    ) -> Tuple[str, str, str]:
        """
        Set up networking for the container.

        Args:
            container_pid: PID of container's init process
            ip: Optional specific IP address

        Returns:
            Tuple of (veth_host, veth_container, ip_address)
        """
        self._setup_called = True
        self.veth_host, self.veth_container, self.ip_address = (
            setup_container_networking(self.container_id, container_pid, ip)
        )
        return self.veth_host, self.veth_container, self.ip_address

    def configure_inside(self) -> None:
        """Configure networking from inside the container."""
        if self.ip_address:
            configure_container_network(self.ip_address)

    def cleanup(self) -> None:
        """Clean up networking resources."""
        if self.veth_host:
            delete_veth(self.veth_host)
        cleanup_container_networking(self.container_id)

    def get_info(self) -> dict:
        """Get network information."""
        return {
            "ip": self.ip_address,
            "mac": self.mac_address,
            "gateway": BRIDGE_IP,
            "veth_host": self.veth_host,
            "veth_container": self.veth_container,
            "bridge": BRIDGE_NAME,
        }
