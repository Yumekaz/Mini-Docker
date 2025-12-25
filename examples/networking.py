#!/usr/bin/env python3
"""
Container Networking Example

This example demonstrates container networking:
1. Creating a network namespace
2. Setting up veth pairs (virtual ethernet)
3. Configuring a bridge
4. Setting up NAT for internet access
5. Testing connectivity

Run with: sudo python3 networking.py
"""

import os
import sys
import subprocess
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mini_docker.container import Container
from mini_docker.network import Network


def print_header(text: str) -> None:
    """Print a formatted header."""
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}\n")


def print_step(text: str) -> None:
    """Print a step indicator."""
    print(f"[+] {text}")


def print_info(text: str) -> None:
    """Print info text."""
    print(f"    {text}")


def print_result(success: bool, message: str) -> None:
    """Print test result."""
    status = "✓ PASS" if success else "✗ FAIL"
    print(f"    [{status}] {message}")


def check_root() -> bool:
    """Check if running as root."""
    if os.geteuid() != 0:
        print("Error: This example requires root privileges.")
        print("Please run with: sudo python3 networking.py")
        return False
    return True


def check_commands() -> bool:
    """Check if required commands are available."""
    required = ["ip", "iptables", "ping"]
    missing = []
    
    for cmd in required:
        result = subprocess.run(
            ["which", cmd],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            missing.append(cmd)
    
    if missing:
        print(f"Error: Missing required commands: {', '.join(missing)}")
        print("Please install: sudo apt install iproute2 iptables iputils-ping")
        return False
    
    return True


def get_rootfs() -> str:
    """Get path to rootfs."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "rootfs"
    )


def run_command(cmd: list, check: bool = True) -> tuple:
    """Run a shell command and return output."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.returncode, result.stdout, result.stderr


def example_network_namespace():
    """
    Example 1: Network Namespace Isolation
    
    Shows that a container has its own network namespace
    with separate interfaces, routing table, etc.
    """
    print_header("Example 1: Network Namespace Isolation")
    
    print_step("Host network interfaces:")
    _, stdout, _ = run_command(["ip", "addr", "show"])
    for line in stdout.split('\n')[:10]:  # First 10 lines
        if line.strip():
            print_info(line)
    
    # Script to show container's network
    net_script = """
echo ""
echo "Container network interfaces:"
ip addr show 2>/dev/null || echo "ip command not available"
echo ""
echo "Container routing table:"
ip route show 2>/dev/null || echo "No routes (isolated)"
echo ""
echo "Note: Container has its own network namespace!"
echo "This is completely isolated from the host."
"""
    
    rootfs = get_rootfs()
    
    print_step("Creating container with network namespace...")
    
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", net_script],
            hostname="net-isolated",
            network=True  # Enable network namespace
        )
        
        exit_code = container.run()
        print_result(True, "Network namespace isolation working!")
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False


def example_veth_pair():
    """
    Example 2: Virtual Ethernet Pairs
    
    Demonstrates creating veth pairs to connect
    the container to the host network.
    """
    print_header("Example 2: Virtual Ethernet Pairs")
    
    print_step("Understanding veth pairs:")
    print_info("veth pairs are like a virtual network cable")
    print_info("One end goes in the container, one stays on the host")
    print_info("")
    print_info("  Host                Container")
    print_info("  ┌────────┐          ┌────────┐")
    print_info("  │ veth0  │◄────────►│  eth0  │")
    print_info("  └────────┘  veth    └────────┘")
    print_info("      │       pair")
    print_info("      ▼")
    print_info("  ┌────────┐")
    print_info("  │ bridge │")
    print_info("  └────────┘")
    
    # Show network setup script
    net_setup_script = """
echo ""
echo "Container network after veth setup:"
echo ""
ip addr show 2>/dev/null || echo "ip not available"
echo ""
echo "Testing loopback:"
ping -c 1 127.0.0.1 2>/dev/null && echo "Loopback OK" || echo "No ping available"
"""
    
    rootfs = get_rootfs()
    
    print_step("Creating container with veth pair...")
    
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", net_setup_script],
            hostname="veth-demo",
            network=True
        )
        
        exit_code = container.run()
        print_result(True, "Veth pair concept demonstrated!")
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False


def example_bridge_network():
    """
    Example 3: Bridge Network
    
    Shows how containers connect through a bridge,
    enabling container-to-container communication.
    """
    print_header("Example 3: Bridge Network")
    
    print_step("Bridge network architecture:")
    print_info("")
    print_info("  Container A        Container B")
    print_info("  ┌────────┐        ┌────────┐")
    print_info("  │ 10.0.0.2│        │10.0.0.3│")
    print_info("  └───┬────┘        └───┬────┘")
    print_info("      │                 │")
    print_info("      └────────┬────────┘")
    print_info("               │")
    print_info("        ┌──────┴──────┐")
    print_info("        │   Bridge    │")
    print_info("        │  10.0.0.1   │")
    print_info("        └──────┬──────┘")
    print_info("               │")
    print_info("        ┌──────┴──────┐")
    print_info("        │    Host     │")
    print_info("        │   eth0      │")
    print_info("        └─────────────┘")
    
    # Script showing bridge connectivity
    bridge_script = """
echo ""
echo "Container network configuration:"
echo ""
ip addr show 2>/dev/null || echo "Cannot show addresses"
echo ""
echo "Trying to reach the bridge (host) at 10.0.0.1..."
ping -c 1 10.0.0.1 2>/dev/null && echo "Bridge reachable!" || echo "Ping not available or bridge not reachable"
"""
    
    rootfs = get_rootfs()
    
    print_step("Creating container connected to bridge...")
    
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", bridge_script],
            hostname="bridge-demo",
            network=True
        )
        
        exit_code = container.run()
        print_result(True, "Bridge network concept demonstrated!")
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False


def example_nat_internet():
    """
    Example 4: NAT for Internet Access
    
    Demonstrates how NAT enables containers to access
    the internet through the host.
    """
    print_header("Example 4: NAT (Network Address Translation)")
    
    print_step("NAT architecture:")
    print_info("")
    print_info("  Container          Host           Internet")
    print_info("  ┌────────┐      ┌────────┐      ┌────────┐")
    print_info("  │10.0.0.2│─────►│  NAT   │─────►│8.8.8.8 │")
    print_info("  └────────┘      │        │      └────────┘")
    print_info("                  │10.0.0.1│")
    print_info("  Source:         └────────┘")
    print_info("  10.0.0.2:12345     │")
    print_info("                     ▼")
    print_info("  Translated to:")
    print_info("  <host-ip>:54321")
    print_info("")
    print_step("NAT allows private IPs to access internet")
    
    # Script testing internet access
    nat_script = """
echo ""
echo "Container IP configuration:"
ip addr show 2>/dev/null | head -20 || echo "Cannot show addresses"
echo ""
echo "Default route:"
ip route show 2>/dev/null | head -5 || echo "No routes"
echo ""
echo "Testing internet connectivity..."
echo "(This requires NAT to be configured on host)"
ping -c 2 8.8.8.8 2>/dev/null && echo "Internet reachable!" || echo "No internet access (NAT may not be configured)"
"""
    
    rootfs = get_rootfs()
    
    print_step("Creating container and testing NAT...")
    
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", nat_script],
            hostname="nat-demo",
            network=True
        )
        
        exit_code = container.run()
        
        print("")
        print_step("To enable NAT on host (if not working):")
        print_info("sudo sysctl -w net.ipv4.ip_forward=1")
        print_info("sudo iptables -t nat -A POSTROUTING -s 10.0.0.0/24 -j MASQUERADE")
        
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False


def example_dns_resolution():
    """
    Example 5: DNS Resolution
    
    Shows how containers can resolve domain names.
    """
    print_header("Example 5: DNS Resolution")
    
    print_step("DNS configuration in containers:")
    print_info("Containers need /etc/resolv.conf to resolve names")
    print_info("")
    print_info("Options:")
    print_info("1. Copy host's /etc/resolv.conf into container")
    print_info("2. Use public DNS (8.8.8.8, 1.1.1.1)")
    print_info("3. Run a DNS server on the bridge")
    
    # Script checking DNS
    dns_script = """
echo ""
echo "DNS configuration:"
cat /etc/resolv.conf 2>/dev/null || echo "No resolv.conf"
echo ""
echo "Note: DNS resolution requires:"
echo "1. resolv.conf in container"
echo "2. Network connectivity to DNS server"
echo "3. UDP port 53 allowed"
"""
    
    rootfs = get_rootfs()
    
    print_step("Checking container DNS configuration...")
    
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", dns_script],
            hostname="dns-demo",
            network=True
        )
        
        exit_code = container.run()
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False


def example_port_mapping():
    """
    Example 6: Port Mapping Concepts
    
    Explains how port mapping works (conceptually).
    """
    print_header("Example 6: Port Mapping (Concepts)")
    
    print_step("Port mapping architecture:")
    print_info("")
    print_info("  External          Host           Container")
    print_info("  ┌────────┐      ┌────────┐      ┌────────┐")
    print_info("  │Client  │─────►│Port    │─────►│Port 80 │")
    print_info("  │        │      │ 8080   │      │(nginx) │")
    print_info("  └────────┘      └────────┘      └────────┘")
    print_info("")
    print_info("  Request to host:8080 is forwarded to container:80")
    print_info("")
    print_step("Port mapping uses iptables DNAT rules:")
    print_info("iptables -t nat -A PREROUTING -p tcp --dport 8080 \\")
    print_info("    -j DNAT --to-destination 10.0.0.2:80")
    print_info("")
    print_info("This is how 'docker run -p 8080:80' works!")
    
    return True


def main():
    """Run all networking examples."""
    print_header("Mini-Docker Networking Examples")
    
    # Check prerequisites
    if not check_root():
        return 1
    
    if not check_commands():
        return 1
    
    rootfs = get_rootfs()
    if not os.path.exists(rootfs):
        print(f"Error: rootfs not found at {rootfs}")
        print("Please run: sudo ./setup.sh")
        return 1
    
    print_step("All prerequisites met!")
    
    # Run examples
    examples = [
        ("Network Namespace", example_network_namespace),
        ("Veth Pairs", example_veth_pair),
        ("Bridge Network", example_bridge_network),
        ("NAT Internet", example_nat_internet),
        ("DNS Resolution", example_dns_resolution),
        ("Port Mapping", example_port_mapping),
    ]
    
    results = []
    for name, func in examples:
        try:
            success = func()
            results.append((name, success))
        except KeyboardInterrupt:
            print("\nInterrupted by user")
            break
        except Exception as e:
            print(f"Error in {name}: {e}")
            results.append((name, False))
    
    # Summary
    print_header("Summary")
    for name, success in results:
        status = "✓ PASSED" if success else "✗ FAILED"
        print(f"  {name}: {status}")
    
    print("")
    print("Container networking key concepts:")
    print("  1. Network namespaces provide isolation")
    print("  2. Veth pairs connect containers to host")
    print("  3. Bridges enable container-to-container communication")
    print("  4. NAT enables internet access")
    print("  5. Port mapping exposes container services")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
