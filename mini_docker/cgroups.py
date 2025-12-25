#!/usr/bin/env python3
"""
Cgroups v2 Resource Limits for Mini-Docker.

Cgroups (Control Groups) provide resource limiting, prioritization,
accounting, and control for processes.

Cgroups v2 unified hierarchy:
    /sys/fs/cgroup/
    └── mini-docker/
        └── <container-id>/
            ├── cgroup.procs        # PIDs in this cgroup
            ├── cpu.max             # CPU quota (quota period)
            ├── memory.max          # Memory hard limit
            ├── memory.current      # Current memory usage
            ├── pids.max            # Maximum number of processes
            └── io.max              # I/O bandwidth limits

Controllers:
- cpu: CPU bandwidth control
- memory: Memory usage limits
- pids: Process number limits
- io: Block I/O control
"""

import os
from typing import Optional, Dict
from mini_docker.utils import write_file, read_file

# Cgroups v2 root path
CGROUP_ROOT = "/sys/fs/cgroup"
MINI_DOCKER_CGROUP = f"{CGROUP_ROOT}/mini-docker"


class CgroupError(Exception):
    """Exception raised for cgroup operations."""
    pass


def is_cgroups_v2() -> bool:
    """
    Check if cgroups v2 is mounted.
    
    Returns:
        True if cgroups v2 is available
    """
    # Check for cgroups v2 unified hierarchy
    return os.path.exists(f"{CGROUP_ROOT}/cgroup.controllers")


def get_available_controllers() -> list:
    """
    Get list of available cgroup controllers.
    
    Returns:
        List of controller names (e.g., ["cpu", "memory", "pids"])
    """
    controllers_file = f"{CGROUP_ROOT}/cgroup.controllers"
    content = read_file(controllers_file)
    if content:
        return content.split()
    return []


def enable_controllers(path: str, controllers: list) -> None:
    """
    Enable controllers for a cgroup path.
    
    Args:
        path: Cgroup path
        controllers: List of controllers to enable
    """
    subtree_control = os.path.join(path, "cgroup.subtree_control")
    if os.path.exists(subtree_control):
        control_str = " ".join(f"+{c}" for c in controllers)
        try:
            write_file(subtree_control, control_str)
        except (IOError, OSError, PermissionError):
            pass  # Some controllers might not be available


def create_cgroup(container_id: str) -> str:
    """
    Create a cgroup for a container.
    
    Args:
        container_id: Container ID
        
    Returns:
        Path to the created cgroup
        
    Raises:
        CgroupError: If cgroup creation fails
    """
    if not is_cgroups_v2():
        raise CgroupError("Cgroups v2 not available")
    
    # Create mini-docker parent cgroup
    os.makedirs(MINI_DOCKER_CGROUP, exist_ok=True)
    
    # Enable controllers on parent
    available = get_available_controllers()
    wanted = ["cpu", "memory", "pids", "io"]
    enable = [c for c in wanted if c in available]
    
    # Enable on cgroup root
    enable_controllers(CGROUP_ROOT, enable)
    enable_controllers(MINI_DOCKER_CGROUP, enable)
    
    # Create container cgroup
    cgroup_path = os.path.join(MINI_DOCKER_CGROUP, container_id)
    os.makedirs(cgroup_path, exist_ok=True)
    
    return cgroup_path


def add_process_to_cgroup(cgroup_path: str, pid: int) -> None:
    """
    Add a process to a cgroup.
    
    Args:
        cgroup_path: Path to the cgroup
        pid: Process ID to add
        
    Raises:
        CgroupError: If adding process fails
    """
    procs_file = os.path.join(cgroup_path, "cgroup.procs")
    if not write_file(procs_file, str(pid)):
        raise CgroupError(f"Failed to add PID {pid} to cgroup")


def set_cpu_limit(
    cgroup_path: str,
    quota_us: int,
    period_us: int = 100000
) -> None:
    """
    Set CPU bandwidth limit.
    
    CPU usage is limited by: quota / period
    For example: 50000/100000 = 50% of one CPU
    
    Args:
        cgroup_path: Path to the cgroup
        quota_us: CPU quota in microseconds
        period_us: Period in microseconds (default 100ms)
        
    Format in cpu.max: "quota period"
        "50000 100000" = 50% of one CPU
        "max 100000" = no limit
    """
    cpu_max = os.path.join(cgroup_path, "cpu.max")
    value = f"{quota_us} {period_us}"
    if not write_file(cpu_max, value):
        # cpu controller might not be available
        pass


def set_memory_limit(cgroup_path: str, limit_bytes: int) -> None:
    """
    Set memory hard limit.
    
    When the limit is reached, the OOM killer may terminate processes.
    
    Args:
        cgroup_path: Path to the cgroup
        limit_bytes: Memory limit in bytes
        
    Format in memory.max: "bytes"
        "104857600" = 100 MB
        "max" = no limit
    """
    memory_max = os.path.join(cgroup_path, "memory.max")
    if not write_file(memory_max, str(limit_bytes)):
        # memory controller might not be available
        pass


def set_pids_limit(cgroup_path: str, max_pids: int) -> None:
    """
    Set maximum number of processes.
    
    Prevents fork bombs by limiting process creation.
    
    Args:
        cgroup_path: Path to the cgroup
        max_pids: Maximum number of processes
        
    Format in pids.max: "count"
        "100" = max 100 processes
        "max" = no limit
    """
    pids_max = os.path.join(cgroup_path, "pids.max")
    if not write_file(pids_max, str(max_pids)):
        # pids controller might not be available
        pass


def set_io_limit(
    cgroup_path: str,
    device: str,
    rbps: Optional[int] = None,
    wbps: Optional[int] = None
) -> None:
    """
    Set I/O bandwidth limits.
    
    Args:
        cgroup_path: Path to the cgroup
        device: Device major:minor (e.g., "8:0" for /dev/sda)
        rbps: Read bytes per second limit
        wbps: Write bytes per second limit
        
    Format in io.max: "major:minor rbps=X wbps=Y"
    """
    io_max = os.path.join(cgroup_path, "io.max")
    parts = [device]
    if rbps is not None:
        parts.append(f"rbps={rbps}")
    if wbps is not None:
        parts.append(f"wbps={wbps}")
    
    value = " ".join(parts)
    write_file(io_max, value)


def get_memory_usage(cgroup_path: str) -> int:
    """
    Get current memory usage.
    
    Args:
        cgroup_path: Path to the cgroup
        
    Returns:
        Memory usage in bytes
    """
    memory_current = os.path.join(cgroup_path, "memory.current")
    content = read_file(memory_current)
    if content:
        return int(content)
    return 0


def get_cpu_stats(cgroup_path: str) -> Dict[str, int]:
    """
    Get CPU statistics.
    
    Args:
        cgroup_path: Path to the cgroup
        
    Returns:
        Dictionary with CPU stats
    """
    cpu_stat = os.path.join(cgroup_path, "cpu.stat")
    content = read_file(cpu_stat)
    stats = {}
    if content:
        for line in content.strip().split('\n'):
            parts = line.split()
            if len(parts) == 2:
                stats[parts[0]] = int(parts[1])
    return stats


def delete_cgroup(cgroup_path: str) -> None:
    """
    Delete a cgroup.
    
    The cgroup must be empty (no processes) before deletion.
    
    Args:
        cgroup_path: Path to the cgroup
    """
    procs_file = os.path.join(cgroup_path, "cgroup.procs")
    if os.path.exists(procs_file):
        try:
            content = read_file(procs_file)
            if content:
                import signal
                for pid_str in content.strip().split('\n'):
                    if pid_str:
                        try:
                            os.kill(int(pid_str), signal.SIGKILL)
                        except (OSError, ValueError):
                            pass
        except (IOError, OSError):
            pass
    
    try:
        os.rmdir(cgroup_path)
    except OSError:
        # Cgroup might not be empty or might not exist
        pass


class Cgroup:
    """
    Cgroup manager for a container.
    
    Example:
        cgroup = Cgroup(container_id)
        cgroup.set_limits(cpu_quota=50000, memory_mb=100, max_pids=100)
        cgroup.add_process(pid)
    """
    
    def __init__(self, container_id: str):
        self.container_id = container_id
        self.path = create_cgroup(container_id)
    
    def add_process(self, pid: int) -> None:
        """Add a process to this cgroup."""
        add_process_to_cgroup(self.path, pid)
    
    def set_limits(
        self,
        cpu_quota: Optional[int] = None,
        cpu_period: int = 100000,
        memory_mb: Optional[int] = None,
        max_pids: Optional[int] = None
    ) -> None:
        """
        Set resource limits.
        
        Args:
            cpu_quota: CPU quota in microseconds
            cpu_period: CPU period in microseconds
            memory_mb: Memory limit in megabytes
            max_pids: Maximum number of processes
        """
        if cpu_quota is not None:
            set_cpu_limit(self.path, cpu_quota, cpu_period)
        
        if memory_mb is not None:
            set_memory_limit(self.path, memory_mb * 1024 * 1024)
        
        if max_pids is not None:
            set_pids_limit(self.path, max_pids)
    
    def get_stats(self) -> Dict:
        """Get current resource usage statistics."""
        return {
            "memory_bytes": get_memory_usage(self.path),
            "cpu": get_cpu_stats(self.path),
        }
    
    def cleanup(self) -> None:
        """Delete this cgroup."""
        delete_cgroup(self.path)
