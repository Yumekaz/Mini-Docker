#!/usr/bin/env python3
"""
Pod Support for Mini-Docker.

Pods are groups of containers that share:
- Network namespace (same IP, localhost communication)
- IPC namespace (shared memory, semaphores)
- UTS namespace (same hostname)

Pod Architecture:
    ┌───────────────────────────────────────────┐
    │                   POD                      │
    │  ┌─────────────┐   ┌─────────────┐        │
    │  │ Container 1 │   │ Container 2 │        │
    │  │   (app)     │   │  (sidecar)  │        │
    │  └─────────────┘   └─────────────┘        │
    │         │                 │               │
    │         └────────┬────────┘               │
    │                  │                        │
    │  ┌───────────────┴───────────────────┐   │
    │  │    Shared Namespaces:              │   │
    │  │    - Network (10.0.0.X)            │   │
    │  │    - IPC                           │   │
    │  │    - UTS (pod-hostname)            │   │
    │  └───────────────────────────────────┘   │
    └───────────────────────────────────────────┘
"""

import os
import json
import time
import signal
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict
from mini_docker.utils import (
    PODS_PATH,
    generate_container_id,
    generate_container_name,
    ensure_directories,
)


class PodError(Exception):
    """Exception raised during pod operations."""

    pass


@dataclass
class PodConfig:
    """Pod configuration."""

    id: str = ""
    name: str = ""

    # Containers in this pod
    containers: List[str] = field(default_factory=list)

    # Shared namespace holder (infra container) PID
    infra_pid: Optional[int] = None

    # Network configuration
    ip_address: Optional[str] = None

    # Shared namespaces
    shared_namespaces: List[str] = field(default_factory=lambda: ["net", "ipc", "uts"])

    # Pod hostname
    hostname: str = ""

    # State
    status: str = "created"
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.id:
            self.id = generate_container_id()
        if not self.name:
            self.name = generate_container_name()
        if not self.hostname:
            self.hostname = self.name


def get_pod_path(pod_id: str) -> str:
    """Get path to pod directory."""
    return os.path.join(PODS_PATH, pod_id)


def pod_exists(pod_id: str) -> bool:
    """Check if pod exists."""
    return os.path.exists(os.path.join(get_pod_path(pod_id), "config.json"))


def save_pod_config(config: PodConfig) -> str:
    """Save pod configuration."""
    ensure_directories()

    pod_path = get_pod_path(config.id)
    os.makedirs(pod_path, exist_ok=True)

    config_path = os.path.join(pod_path, "config.json")

    with open(config_path, "w") as f:
        json.dump(asdict(config), f, indent=2)

    return config_path


def load_pod_config(pod_id: str) -> Optional[PodConfig]:
    """Load pod configuration."""
    full_id = find_pod_id(pod_id)
    if not full_id:
        return None

    config_path = os.path.join(get_pod_path(full_id), "config.json")

    if not os.path.exists(config_path):
        return None

    try:
        with open(config_path, "r") as f:
            data = json.load(f)
        return PodConfig(**data)
    except (json.JSONDecodeError, TypeError):
        return None


def delete_pod_config(pod_id: str) -> bool:
    """Delete pod configuration."""
    import shutil

    full_id = find_pod_id(pod_id)
    if not full_id:
        return False

    config = load_pod_config(full_id)
    if config and config.infra_pid:
        try:
            os.kill(config.infra_pid, signal.SIGTERM)
            time.sleep(0.5)
            os.kill(config.infra_pid, signal.SIGKILL)
        except OSError:
            pass

    try:
        shutil.rmtree(get_pod_path(full_id))
        return True
    except (OSError, IOError):
        return False


def list_pods() -> List[PodConfig]:
    """List all pods."""
    ensure_directories()

    pods = []

    if not os.path.exists(PODS_PATH):
        return pods

    for pod_id in os.listdir(PODS_PATH):
        config = load_pod_config(pod_id)
        if config:
            pods.append(config)

    pods.sort(key=lambda p: p.created_at, reverse=True)
    return pods


def find_pod_id(prefix: str) -> Optional[str]:
    """Find pod by ID prefix or name."""
    if not os.path.exists(PODS_PATH):
        return None

    for pod_id in os.listdir(PODS_PATH):
        if pod_id.startswith(prefix):
            return pod_id

        config = load_pod_config(pod_id)
        if config and config.name == prefix:
            return pod_id

    return None


def add_container_to_pod(pod_id: str, container_id: str) -> bool:
    """Add a container to a pod."""
    config = load_pod_config(pod_id)
    if not config:
        return False

    if container_id not in config.containers:
        config.containers.append(container_id)
        save_pod_config(config)

    return True


def remove_container_from_pod(pod_id: str, container_id: str) -> bool:
    """Remove a container from a pod."""
    config = load_pod_config(pod_id)
    if not config:
        return False

    if container_id in config.containers:
        config.containers.remove(container_id)
        save_pod_config(config)

    return True


class PodManager:
    """
    Pod manager for Mini-Docker.

    Example:
        pods = PodManager()
        pod = pods.create("my-pod")
        pods.add_container(pod.id, container_id)
    """

    def __init__(self):
        ensure_directories()

    def create(self, name: Optional[str] = None, **kwargs) -> PodConfig:
        """Create a new pod."""
        config = PodConfig(name=name or "", **kwargs)
        save_pod_config(config)

        self._start_infra_container(config)

        return config

    def _start_infra_container(self, config: PodConfig) -> None:
        """Start the infra container that holds shared namespaces."""
        import subprocess

        # Fork a process that just sleeps to hold namespaces
        pid = os.fork()
        if pid == 0:
            # Child process - become the infra container
            try:
                from mini_docker.namespaces import create_namespaces, sethostname

                # Create shared namespaces
                create_namespaces(config.shared_namespaces, hostname=config.hostname)

                # Set hostname
                if config.hostname:
                    try:
                        sethostname(config.hostname)
                    except Exception:
                        pass

                # Sleep forever (will be killed when pod is deleted)
                while True:
                    time.sleep(3600)
            except Exception:
                os._exit(1)
        else:
            # Parent - save infra PID
            config.infra_pid = pid
            config.status = "running"
            save_pod_config(config)

    def get(self, pod_id: str) -> Optional[PodConfig]:
        """Get pod configuration."""
        return load_pod_config(pod_id)

    def update(self, config: PodConfig) -> None:
        """Update pod configuration."""
        save_pod_config(config)

    def delete(self, pod_id: str, force: bool = False) -> bool:
        """Delete a pod.

        Args:
            pod_id: Pod ID or name
            force: Force deletion even if pod has running containers

        Returns:
            True if deleted successfully

        Raises:
            PodError: If pod has running containers and force is False
        """
        config = load_pod_config(pod_id)
        if not config:
            return False

        # Check if pod has running containers
        if config.containers and not force:
            raise PodError(
                f"Pod has {len(config.containers)} containers. Use --force to remove."
            )

        return delete_pod_config(pod_id)

    def list(self) -> List[PodConfig]:
        """List all pods."""
        return list_pods()

    def add_container(self, pod_id: str, container_id: str) -> bool:
        """Add container to pod."""
        return add_container_to_pod(pod_id, container_id)

    def remove_container(self, pod_id: str, container_id: str) -> bool:
        """Remove container from pod."""
        return remove_container_from_pod(pod_id, container_id)

    def set_infra_pid(self, pod_id: str, pid: int) -> bool:
        """Set the infra container PID."""
        config = load_pod_config(pod_id)
        if not config:
            return False
        config.infra_pid = pid
        save_pod_config(config)
        return True

    def get_shared_ns_paths(self, pod_id: str) -> Dict[str, str]:
        """Get paths to shared namespace files."""
        config = load_pod_config(pod_id)
        if not config or not config.infra_pid:
            return {}

        try:
            os.kill(config.infra_pid, 0)
        except OSError:
            # Process is dead, clear infra_pid
            config.infra_pid = None
            config.status = "stopped"
            save_pod_config(config)
            return {}

        paths = {}
        for ns in config.shared_namespaces:
            ns_path = f"/proc/{config.infra_pid}/ns/{ns}"
            if os.path.exists(ns_path):
                paths[ns] = ns_path

        return paths
