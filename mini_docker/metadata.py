#!/usr/bin/env python3
"""
Container metadata storage for Mini-Docker.

Stores container configuration and state in JSON format:
/var/lib/mini-docker/containers/<id>/config.json
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from mini_docker.utils import (
    CONTAINERS_PATH,
    ensure_directories,
    generate_container_id,
    generate_container_name,
    get_container_path,
    is_process_alive,
)


@dataclass
class NetworkConfig:
    """Network configuration for a container."""

    ip: Optional[str] = None
    mac: Optional[str] = None
    gateway: str = "10.0.0.1"
    veth_host: Optional[str] = None
    veth_container: Optional[str] = None
    bridge: str = "mini-docker0"
    ports: List[str] = field(default_factory=list)


@dataclass
class ResourceLimits:
    """Resource limits for a container."""

    cpu_quota: Optional[int] = None
    cpu_period: int = 100000
    memory_mb: Optional[int] = None
    max_pids: Optional[int] = None


@dataclass
class ContainerConfig:
    """Complete container configuration."""

    id: str = ""
    name: str = ""
    rootfs: str = ""
    command: List[str] = field(default_factory=list)

    overlay_lower: str = ""
    overlay_upper: str = ""
    overlay_work: str = ""
    overlay_merged: str = ""
    use_overlay: bool = True

    resources: ResourceLimits = field(default_factory=ResourceLimits)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    namespaces: List[str] = field(default_factory=lambda: ["pid", "uts", "mnt", "ipc"])

    hostname: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    workdir: str = "/"
    user: str = "root"
    uid: Optional[int] = None
    gid: Optional[int] = None

    rootless: bool = False
    pod_id: Optional[str] = None
    network_enabled: bool = False
    volumes: List[Dict[str, str]] = field(default_factory=list)
    auto_remove: bool = False
    detach: bool = False
    interactive: bool = False
    tty: bool = False

    capabilities: List[str] = field(default_factory=list)
    seccomp_enabled: bool = True

    status: str = "created"
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


def _container_config_path(container_id: str) -> str:
    return os.path.join(get_container_path(container_id), "config.json")


def _list_container_ids() -> List[str]:
    if not os.path.exists(CONTAINERS_PATH):
        return []
    return sorted(os.listdir(CONTAINERS_PATH))


def _read_container_data(container_id: str) -> Optional[Dict]:
    config_path = _container_config_path(container_id)
    if not os.path.exists(config_path):
        return None

    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _hydrate_container_config(data: Dict) -> Optional[ContainerConfig]:
    try:
        if "resources" in data and isinstance(data["resources"], dict):
            data["resources"] = ResourceLimits(**data["resources"])
        if "network" in data and isinstance(data["network"], dict):
            data["network"] = NetworkConfig(**data["network"])
        return ContainerConfig(**data)
    except (TypeError, KeyError):
        return None


def _refresh_container_state(config: ContainerConfig) -> ContainerConfig:
    if config.status != "running":
        return config

    if not is_process_alive(config.pid):
        config.status = "stopped"
        config.pid = None
        if config.finished_at is None:
            config.finished_at = time.time()
        save_container_config(config)

    return config


def _load_container_config_by_id(
    container_id: str, refresh_state: bool = True
) -> Optional[ContainerConfig]:
    data = _read_container_data(container_id)
    if not data:
        return None

    config = _hydrate_container_config(data)
    if not config:
        return None

    if refresh_state:
        config = _refresh_container_state(config)

    return config


def container_exists(container_id: str) -> bool:
    """Check if a container exists."""
    return os.path.exists(_container_config_path(container_id))


def save_container_config(config: ContainerConfig) -> str:
    """Save container configuration to disk."""
    ensure_directories()

    container_path = get_container_path(config.id)
    os.makedirs(container_path, exist_ok=True)

    config_path = _container_config_path(config.id)
    with open(config_path, "w") as f:
        json.dump(asdict(config), f, indent=2)

    return config_path


def load_container_config(container_id: str) -> Optional[ContainerConfig]:
    """Load container configuration by full ID, prefix, or name."""
    full_id = find_container_id(container_id)
    if not full_id:
        return None
    return _load_container_config_by_id(full_id)


def update_container_status(
    container_id: str,
    status: str,
    pid: Optional[int] = None,
    exit_code: Optional[int] = None,
) -> bool:
    """Update container status."""
    full_id = find_container_id(container_id)
    if not full_id:
        return False

    config = _load_container_config_by_id(full_id, refresh_state=False)
    if not config:
        return False

    config.status = status

    if status == "running":
        if pid is not None:
            config.pid = pid
        if config.started_at is None:
            config.started_at = time.time()

    if status == "stopped":
        config.pid = None
        config.finished_at = time.time()
        if exit_code is not None:
            config.exit_code = exit_code

    save_container_config(config)
    return True


def delete_container_config(container_id: str) -> bool:
    """Delete container configuration and directory."""
    import shutil

    full_id = find_container_id(container_id)
    if not full_id:
        return False

    try:
        shutil.rmtree(get_container_path(full_id))
        return True
    except (OSError, IOError):
        return False


def list_containers(all_containers: bool = False) -> List[ContainerConfig]:
    """List containers."""
    ensure_directories()

    containers = []
    for container_id in _list_container_ids():
        config = _load_container_config_by_id(container_id)
        if not config:
            continue
        if all_containers or config.status == "running":
            containers.append(config)

    containers.sort(key=lambda c: c.created_at, reverse=True)
    return containers


def find_container_id(prefix: str) -> Optional[str]:
    """Find a full container ID from an ID prefix or container name."""
    if not prefix:
        return None

    if container_exists(prefix):
        return prefix

    container_ids = _list_container_ids()

    for container_id in container_ids:
        if container_id.startswith(prefix):
            return container_id

    for container_id in container_ids:
        data = _read_container_data(container_id)
        if data and data.get("name") == prefix:
            return container_id

    return None


def get_container_log_path(container_id: str) -> str:
    """Get path to container log file."""
    return os.path.join(get_container_path(container_id), "container.log")


class MetadataStore:
    """Metadata store manager."""

    def __init__(self):
        ensure_directories()

    def create(self, rootfs: str, command: List[str], **kwargs) -> ContainerConfig:
        config = ContainerConfig(rootfs=rootfs, command=command, **kwargs)
        save_container_config(config)
        return config

    def get(self, container_id: str) -> Optional[ContainerConfig]:
        return load_container_config(container_id)

    def update(self, config: ContainerConfig) -> None:
        save_container_config(config)

    def update_status(self, container_id: str, status: str, **kwargs) -> bool:
        return update_container_status(container_id, status, **kwargs)

    def delete(self, container_id: str) -> bool:
        return delete_container_config(container_id)

    def list(self, all_containers: bool = False) -> List[ContainerConfig]:
        return list_containers(all_containers)

    def find(self, prefix: str) -> Optional[str]:
        return find_container_id(prefix)
