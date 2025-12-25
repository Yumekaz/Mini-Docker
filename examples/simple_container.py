#!/usr/bin/env python3
"""
Simple Container Example

This example demonstrates the basic steps to create a container:
1. Create Linux namespaces for isolation
2. Set up the filesystem with OverlayFS
3. Configure security (seccomp, capabilities)
4. Execute a command inside the container
5. Clean up resources

Run with: sudo python3 simple_container.py
"""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mini_docker.capabilities import Capabilities
from mini_docker.cgroups import Cgroup
from mini_docker.container import Container
from mini_docker.filesystem import Filesystem
from mini_docker.namespaces import Namespace
from mini_docker.seccomp import Seccomp


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


def check_root() -> bool:
    """Check if running as root."""
    if os.geteuid() != 0:
        print("Error: This example requires root privileges.")
        print("Please run with: sudo python3 simple_container.py")
        return False
    return True


def check_rootfs() -> bool:
    """Check if rootfs exists."""
    rootfs_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rootfs"
    )
    if not os.path.exists(rootfs_path):
        print(f"Error: rootfs not found at {rootfs_path}")
        print("Please run: sudo ./setup.sh")
        return False
    return True


def example_basic_container():
    """
    Example 1: Run a simple command in a container.

    This creates a fully isolated container and runs 'echo Hello'.
    """
    print_header("Example 1: Basic Container")

    print_step("Creating container...")

    # Get rootfs path
    rootfs = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rootfs"
    )

    # Create and run container
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/echo", "Hello from Mini-Docker!"],
            hostname="basic-container",
        )

        print_step("Running container...")
        exit_code = container.run()

        print_step(f"Container exited with code: {exit_code}")

    except Exception as e:
        print(f"Error: {e}")
        return False

    return True


def example_interactive_info():
    """
    Example 2: Show container information.

    This runs a shell script that displays information about
    the container environment.
    """
    print_header("Example 2: Container Information")

    rootfs = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rootfs"
    )

    # Script to show container info
    info_script = """
echo "=== Container Information ==="
echo ""
echo "PID: $$"
echo "Hostname: $(hostname)"
echo "User: $(whoami)"
echo "Working Directory: $(pwd)"
echo ""
echo "=== Filesystem ==="
echo "Root contents:"
ls -la /
echo ""
echo "=== Processes ==="
ps aux 2>/dev/null || echo "ps not available"
echo ""
echo "=== Namespaces ==="
ls -la /proc/self/ns/ 2>/dev/null || echo "Cannot list namespaces"
"""

    print_step("Creating container with info script...")

    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", info_script],
            hostname="info-container",
        )

        print_step("Running container...")
        print("")
        exit_code = container.run()

        print_step(f"Container exited with code: {exit_code}")

    except Exception as e:
        print(f"Error: {e}")
        return False

    return True


def example_isolation_demo():
    """
    Example 3: Demonstrate namespace isolation.

    Shows that the container has its own:
    - PID namespace (PID 1)
    - UTS namespace (different hostname)
    - Mount namespace (different filesystem view)
    """
    print_header("Example 3: Namespace Isolation Demo")

    rootfs = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rootfs"
    )

    print_step("Host information:")
    print_info(f"Host PID: {os.getpid()}")
    print_info(f"Host hostname: {os.uname().nodename}")

    # Script to show isolation
    isolation_script = """
echo ""
echo "Container information:"
echo "  Container PID: $$"
echo "  Container hostname: $(hostname)"
echo ""
echo "Demonstrating isolation:"
echo "  - Container sees PID 1 (it's init!)"
echo "  - Container has different hostname"
echo "  - Container has isolated filesystem"
"""

    print_step("Creating isolated container...")

    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", isolation_script],
            hostname="isolated-container",
        )

        exit_code = container.run()
        print_step(f"Container exited with code: {exit_code}")

    except Exception as e:
        print(f"Error: {e}")
        return False

    return True


def example_filesystem_isolation():
    """
    Example 4: Demonstrate filesystem isolation.

    Shows that changes in the container don't affect the host.
    """
    print_header("Example 4: Filesystem Isolation Demo")

    rootfs = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rootfs"
    )

    # Script that creates a file in the container
    fs_script = """
echo "Creating file in container..."
echo "This file only exists in the container" > /container-test-file.txt
echo "File created:"
cat /container-test-file.txt
echo ""
echo "File location:"
ls -la /container-test-file.txt
"""

    print_step("Creating container that writes a file...")

    try:
        container = Container(
            rootfs=rootfs, command=["/bin/sh", "-c", fs_script], hostname="fs-demo"
        )

        exit_code = container.run()

        print("")
        print_step("Checking if file exists on host...")
        test_file = "/container-test-file.txt"
        if os.path.exists(test_file):
            print_info("WARNING: File leaked to host! (This shouldn't happen)")
        else:
            print_info("SUCCESS: File does NOT exist on host")
            print_info("Filesystem isolation working correctly!")

    except Exception as e:
        print(f"Error: {e}")
        return False

    return True


def main():
    """Run all examples."""
    print_header("Mini-Docker Simple Container Examples")

    # Check prerequisites
    if not check_root():
        return 1

    if not check_rootfs():
        return 1

    print_step("All prerequisites met!")

    # Run examples
    examples = [
        ("Basic Container", example_basic_container),
        ("Container Information", example_interactive_info),
        ("Namespace Isolation", example_isolation_demo),
        ("Filesystem Isolation", example_filesystem_isolation),
    ]

    results = []
    for name, func in examples:
        try:
            success = func()
            results.append((name, success))
        except Exception as e:
            print(f"Error in {name}: {e}")
            results.append((name, False))

    # Summary
    print_header("Summary")
    for name, success in results:
        status = "✓ PASSED" if success else "✗ FAILED"
        print(f"  {name}: {status}")

    failed = sum(1 for _, success in results if not success)
    if failed:
        print(f"\n{failed} example(s) failed")
        return 1
    else:
        print("\nAll examples passed!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
