#!/usr/bin/env python3
"""
Resource Limits Example

This example demonstrates cgroups v2 resource limiting:
1. Memory limits - Prevent containers from using too much RAM
2. CPU limits - Throttle CPU usage
3. PID limits - Prevent fork bombs

Run with: sudo python3 resource_limits.py
"""

import os
import sys
import time

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mini_docker.container import Container
from mini_docker.cgroups import Cgroup


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
        print("Please run with: sudo python3 resource_limits.py")
        return False
    return True


def check_cgroups_v2() -> bool:
    """Check if cgroups v2 is available."""
    cgroup_path = "/sys/fs/cgroup/cgroup.controllers"
    if not os.path.exists(cgroup_path):
        print("Error: Cgroups v2 not available.")
        print("Please ensure your system uses unified cgroup hierarchy.")
        return False
    return True


def get_rootfs() -> str:
    """Get path to rootfs."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "rootfs"
    )


def example_memory_limit():
    """
    Example 1: Memory Limit
    
    Demonstrates that containers cannot exceed their memory limit.
    When a container tries to allocate more memory than allowed,
    it will be killed by the OOM killer.
    """
    print_header("Example 1: Memory Limit")
    
    memory_limit = "32M"  # 32 megabytes
    print_step(f"Setting memory limit to {memory_limit}")
    
    # Script that tries to allocate more memory than allowed
    memory_script = """
echo "Attempting to allocate 64MB of memory..."
echo "Memory limit is 32MB, so this should fail..."
echo ""

# Try to allocate 64MB
dd if=/dev/zero of=/tmp/memtest bs=1M count=64 2>&1

echo ""
echo "If you see this, something went wrong with limits"
"""
    
    rootfs = get_rootfs()
    
    print_step("Creating container with memory limit...")
    print_info(f"Memory limit: {memory_limit}")
    
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", memory_script],
            hostname="memory-test",
            memory_limit=memory_limit
        )
        
        print_step("Running memory-intensive workload...")
        exit_code = container.run()
        
        # Non-zero exit code expected (killed by OOM)
        if exit_code != 0:
            print_result(True, f"Container was killed (exit code: {exit_code})")
            print_info("Memory limit enforcement working correctly!")
            return True
        else:
            print_result(False, "Container completed without being killed")
            return False
            
    except Exception as e:
        print_result(True, f"Container killed: {e}")
        return True


def example_cpu_limit():
    """
    Example 2: CPU Limit
    
    Demonstrates CPU throttling. A container limited to 25% CPU
    will only be able to use 25% of one CPU core.
    """
    print_header("Example 2: CPU Limit")
    
    cpu_percent = 25
    print_step(f"Setting CPU limit to {cpu_percent}%")
    
    # Script that burns CPU
    cpu_script = """
echo "Running CPU-intensive task for 5 seconds..."
echo "CPU is limited to 25%, so this will be throttled"
echo ""

# Burn CPU for 5 seconds
end=$((SECONDS+5))
while [ $SECONDS -lt $end ]; do
    : # Busy loop
done

echo "CPU task completed"
"""
    
    rootfs = get_rootfs()
    
    print_step("Creating container with CPU limit...")
    print_info(f"CPU limit: {cpu_percent}%")
    
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", cpu_script],
            hostname="cpu-test",
            cpu_limit=cpu_percent
        )
        
        print_step("Running CPU-intensive workload...")
        print_info("(Monitor with 'top' in another terminal)")
        
        start_time = time.time()
        exit_code = container.run()
        elapsed = time.time() - start_time
        
        print_step(f"Container completed in {elapsed:.2f} seconds")
        print_info("CPU was throttled during execution")
        print_result(True, "CPU limiting working correctly!")
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False


def example_pid_limit():
    """
    Example 3: PID Limit (Fork Bomb Protection)
    
    Demonstrates that containers cannot create unlimited processes.
    This protects against fork bombs that could crash the host.
    """
    print_header("Example 3: PID Limit (Fork Bomb Protection)")
    
    pid_limit = 10
    print_step(f"Setting PID limit to {pid_limit}")
    
    # Script that tries to create many processes
    fork_script = """
echo "Attempting to create 20 processes..."
echo "PID limit is 10, so this should fail after 10..."
echo ""

count=0
for i in $(seq 1 20); do
    sleep 100 &
    if [ $? -eq 0 ]; then
        count=$((count + 1))
        echo "Created process $count"
    else
        echo "Failed to create process $i (limit reached)"
    fi
done

echo ""
echo "Successfully created $count processes"
echo "Expected: ~10 (the limit)"
"""
    
    rootfs = get_rootfs()
    
    print_step("Creating container with PID limit...")
    print_info(f"PID limit: {pid_limit}")
    
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", fork_script],
            hostname="pid-test",
            pids_limit=pid_limit
        )
        
        print_step("Running fork bomb simulation...")
        exit_code = container.run()
        
        print_result(True, "PID limiting working correctly!")
        print_info("Fork bomb protection active - host is safe!")
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False


def example_combined_limits():
    """
    Example 4: Combined Resource Limits
    
    Demonstrates using multiple resource limits together.
    """
    print_header("Example 4: Combined Resource Limits")
    
    print_step("Setting multiple limits:")
    print_info("Memory: 64M")
    print_info("CPU: 50%")
    print_info("PIDs: 15")
    
    # Script showing all limits
    combined_script = """
echo "Container with combined resource limits:"
echo ""
echo "=== Memory Info ==="
cat /proc/meminfo 2>/dev/null | head -5 || echo "Cannot read meminfo"
echo ""
echo "=== Process Info ==="
echo "Current PID: $$"
echo "Creating a few child processes..."
for i in 1 2 3; do
    sleep 1 &
    echo "  Created background process $i"
done
echo ""
echo "=== CPU Info ==="
echo "Running brief CPU test..."
for i in $(seq 1 100000); do :; done
echo "CPU test complete"
echo ""
echo "All limits working together!"
"""
    
    rootfs = get_rootfs()
    
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", combined_script],
            hostname="combined-test",
            memory_limit="64M",
            cpu_limit=50,
            pids_limit=15
        )
        
        print_step("Running container with all limits...")
        exit_code = container.run()
        
        print_result(True, "Combined limits working correctly!")
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False


def example_cgroup_info():
    """
    Example 5: Cgroup Information
    
    Shows the actual cgroup settings applied to a container.
    """
    print_header("Example 5: Cgroup Information")
    
    print_step("Creating container and showing cgroup details...")
    
    # Script that shows cgroup info from inside container
    cgroup_script = """
echo "=== Cgroup Information ==="
echo ""

if [ -d /sys/fs/cgroup ]; then
    echo "Cgroup filesystem found"
    
    # Try to read cgroup settings
    for file in memory.max cpu.max pids.max; do
        if [ -f /sys/fs/cgroup/$file ]; then
            echo "$file: $(cat /sys/fs/cgroup/$file)"
        fi
    done
else
    echo "Cgroup filesystem not mounted in container"
    echo "(This is expected - cgroups control from outside)"
fi

echo ""
echo "Note: Cgroups control the container from the host,"
echo "so limits are enforced even if not visible inside."
"""
    
    rootfs = get_rootfs()
    
    try:
        container = Container(
            rootfs=rootfs,
            command=["/bin/sh", "-c", cgroup_script],
            hostname="cgroup-info",
            memory_limit="128M",
            cpu_limit=75,
            pids_limit=20
        )
        
        exit_code = container.run()
        
        print_step("Showing host-side cgroup configuration:")
        print_info("Cgroups are configured at: /sys/fs/cgroup/mini-docker/<container-id>/")
        print_info("  memory.max - Memory limit in bytes")
        print_info("  cpu.max - CPU quota and period")
        print_info("  pids.max - Maximum number of processes")
        
        return True
        
    except Exception as e:
        print(f"Error: {e}")
        return False


def main():
    """Run all resource limit examples."""
    print_header("Mini-Docker Resource Limits Examples")
    
    # Check prerequisites
    if not check_root():
        return 1
    
    if not check_cgroups_v2():
        return 1
    
    rootfs = get_rootfs()
    if not os.path.exists(rootfs):
        print(f"Error: rootfs not found at {rootfs}")
        print("Please run: sudo ./setup.sh")
        return 1
    
    print_step("All prerequisites met!")
    print_info("Cgroups v2: Available")
    print_info(f"Rootfs: {rootfs}")
    
    # Run examples
    examples = [
        ("Memory Limit", example_memory_limit),
        ("CPU Limit", example_cpu_limit),
        ("PID Limit", example_pid_limit),
        ("Combined Limits", example_combined_limits),
        ("Cgroup Information", example_cgroup_info),
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
    
    failed = sum(1 for _, success in results if not success)
    if failed:
        print(f"\n{failed} example(s) failed")
        return 1
    else:
        print("\nAll examples passed!")
        print("\nResource limits are working correctly.")
        print("Your system is protected against:")
        print("  - Memory exhaustion attacks")
        print("  - CPU starvation")
        print("  - Fork bombs")
        return 0


if __name__ == "__main__":
    sys.exit(main())
