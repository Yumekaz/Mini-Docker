#!/usr/bin/env python3
"""
Container Metadata Storage for Mini-Docker.

Stores container configuration and state in JSON format:
/var/lib/mini-docker/containers/<id>/config.json

Metadata includes:
- Container ID and name
- Rootfs and overlay paths
- Resource limits (CPU, memory)
- Network configuration
- Creation time and status
- PID of container process
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from mini_docker.utils import (CONTAINERS_PATH, ensure_directories,
                               generate_container_id, generate_container_name,
                               get_container_path)


@dataclass
class NetworkConfig:
    """Network configuration for a container."""

    ip: Optional[str] = None
    mac: Optional[str] = None
    gateway: str = "10.0.0.1"
    veth_host: Optional[str] = None
    veth_container: Optional[str] = None
    bridge: str = "mini-docker0"


@dataclass
class ResourceLimits:
    """Resource limits for a container."""

    cpu_quota: Optional[int] = None  # microseconds
    cpu_period: int = 100000  # microseconds
    memory_mb: Optional[int] = None
    max_pids: Optional[int] = None


@dataclass
class ContainerConfig:
    """Complete container configuration."""

    id: str = ""
    name: str = ""
    rootfs: str = ""
    command: List[str] = field(default_factory=list)

    # Overlay filesystem paths
    overlay_lower: str = ""
    overlay_upper: str = ""
    overlay_work: str = ""
    overlay_merged: str = ""

    # Use overlay or chroot
    use_overlay: bool = True

    # Resource limits
    resources: ResourceLimits = field(default_factory=ResourceLimits)

    # Network configuration
    network: NetworkConfig = field(default_factory=NetworkConfig)

    # Namespaces to create
    namespaces: List[str] = field(
        default_factory=lambda: ["pid", "uts", "mnt", "ipc", "net"]
    )

    # Container hostname
    hostname: str = ""

    # Environment variables
    env: Dict[str, str] = field(default_factory=dict)

    # Working directory
    workdir: str = "/"

    # User to run as
    user: str = "root"

    # Rootless mode
    rootless: bool = False

    # Pod membership
    pod_id: Optional[str] = None

    # Security
    capabilities: List[str] = field(default_factory=list)
    seccomp_enabled: bool = True

    # State
    status: str = "created"  # created, running, stopped, paused
    pid: Optional[int] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    exit_code: Optional[int] = None

    def __post_init__(self):
        if not self.id:
            self.id = generate_container_id()
        if not self.name:
            self.name = generate_container_name()
        if not self.hostname:
            self.hostname = self.name


def container_exists(container_id: str) -> bool:
    """Check if a container exists."""
    config_path = os.path.join(get_container_path(container_id), "config.json")
    return os.path.exists(config_path)


def save_container_config(config: ContainerConfig) -> str:
    """
    Save container configuration to disk.

    Args:
        config: ContainerConfig instance

    Returns:
        Path to the config file
    """
    ensure_directories()

    container_path = get_container_path(config.id)
    os.makedirs(container_path, exist_ok=True)

    config_path = os.path.join(container_path, "config.json")

    # Convert to dict, handling nested dataclasses
    data = asdict(config)

    with open(config_path, "w") as f:
        json.dump(data, f, indent=2)

    return config_path


def load_container_config(container_id: str) -> Optional[ContainerConfig]:
    """
    Load container configuration from disk.

    Args:
        container_id: Container ID (or prefix)

    Returns:
        ContainerConfig instance or None if not found
    """
    # Find container by ID or prefix
    full_id = find_container_id(container_id)
    if not full_id:
        return None

    config_path = os.path.join(get_container_path(full_id), "config.json")

    if not os.path.exists(config_path):
        return None

    try:
        with open(config_path, "r") as f:
            data = json.load(f)

        # Convert nested dicts to dataclasses
        if "resources" in data and isinstance(data["resources"], dict):
            data["resources"] = ResourceLimits(**data["resources"])
        if "network" in data and isinstance(data["network"], dict):
            data["network"] = NetworkConfig(**data["network"])

        return ContainerConfig(**data)
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def update_container_status(
    container_id: str,
    status: str,
    pid: Optional[int] = None,
    exit_code: Optional[int] = None,
) -> bool:
    """
    Update container status.

    Args:
        container_id: Container ID
        status: New status
        pid: Process ID (for running containers)
        exit_code: Exit code (for stopped containers)

    Returns:
        True if successful
    """
    config = load_container_config(container_id)
    if not config:
        return False

    config.status = status

    if pid is not None:
        config.pid = pid

    if status == "running" and config.started_at is None:
        config.started_at = time.time()

    if status == "stopped":
        config.finished_at = time.time()
        if exit_code is not None:
            config.exit_code = exit_code

    save_container_config(config)
    return True


def delete_container_config(container_id: str) -> bool:
    """
    Delete container configuration and directory.

    Args:
        container_id: Container ID

    Returns:
        True if successful
    """
    import shutil

    full_id = find_container_id(container_id)
    if not full_id:
        return False

    container_path = get_container_path(full_id)

    try:
        shutil.rmtree(container_path)
        return True
    except (OSError, IOError):
        return False


def list_containers(all_containers: bool = False) -> List[ContainerConfig]:
    """
    List all containers.

    Args:
        all_containers: If True, include stopped containers

    Returns:
        List of ContainerConfig instances
    """
    ensure_directories()

    containers = []

    if not os.path.exists(CONTAINERS_PATH):
        return containers

    for container_id in os.listdir(CONTAINERS_PATH):
        config = load_container_config(container_id)
        if config:
            if all_containers or config.status == "running":
                containers.append(config)

    # Sort by creation time
    containers.sort(key=lambda c: c.created_at, reverse=True)

    return containers


def find_container_id(prefix: str) -> Optional[str]:
    """
    Find full container ID from prefix.

    Args:
        prefix: Container ID prefix or name

    Returns:
        Full container ID or None
    """
    if not os.path.exists(CONTAINERS_PATH):
        return None

    for container_id in os.listdir(CONTAINERS_PATH):
        # Match by ID prefix
        if container_id.startswith(prefix):
            return container_id

        # Match by name
        config = load_container_config(container_id)
        if config and config.name == prefix:
            return container_id

    return None


def get_container_log_path(container_id: str) -> str:
    """Get path to container log file."""
    return os.path.join(get_container_path(container_id), "container.log")


class MetadataStore:
    """
    Metadata store manager.

    Example:
        store = MetadataStore()
        config = store.create("rootfs", ["sh"])
        store.update_status(config.id, "running", pid=1234)
        containers = store.list()
    """

    def __init__(self):
        ensure_directories()

    def create(self, rootfs: str, command: List[str], **kwargs) -> ContainerConfig:
        """Create a new container configuration."""
        config = ContainerConfig(rootfs=rootfs, command=command, **kwargs)
        save_container_config(config)
        return config

    def get(self, container_id: str) -> Optional[ContainerConfig]:
        """Get container configuration."""
        return load_container_config(container_id)

    def update(self, config: ContainerConfig) -> None:
        """Update container configuration."""
        save_container_config(config)

    def update_status(self, container_id: str, status: str, **kwargs) -> bool:
        """Update container status."""
        return update_container_status(container_id, status, **kwargs)

    def delete(self, container_id: str) -> bool:
        """Delete container."""
        return delete_container_config(container_id)

    def list(self, all_containers: bool = False) -> List[ContainerConfig]:
        """List containers."""
        return list_containers(all_containers)

    def find(self, prefix: str) -> Optional[str]:
        """Find container by ID prefix or name."""
        return find_container_id(prefix)
