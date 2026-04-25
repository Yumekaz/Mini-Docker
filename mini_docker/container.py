#!/usr/bin/env python3
"""
Container Management for Mini-Docker.

This is the core module that brings together all isolation mechanisms:
- Namespaces (PID, UTS, Mount, IPC, Network, User)
- Filesystem (chroot or OverlayFS)
- Cgroups (resource limits)
- Network (veth pairs, bridge)
- Seccomp (syscall filtering)
- Capabilities (privilege reduction)

Container Lifecycle:
    create → start → running → stop → stopped → remove
"""

import errno
import os
import signal
import sys
from typing import Dict, List, Optional

from mini_docker.capabilities import Capabilities
from mini_docker.cgroups import Cgroup, delete_cgroup
from mini_docker.filesystem import (
    cleanup_overlay,
    setup_chroot_filesystem,
    setup_minimal_dev,
    setup_overlay_filesystem,
    setup_pivot_root,
)
from mini_docker.logger import ContainerLogger
from mini_docker.metadata import (
    ContainerConfig,
    MetadataStore,
    load_container_config,
    save_container_config,
    update_container_status,
)
from mini_docker.namespaces import (
    create_namespaces,
    enter_all_namespaces,
    setup_user_namespace,
)
from mini_docker.network import Network, configure_container_network
from mini_docker.pod import PodManager, load_pod_config
from mini_docker.seccomp import Seccomp
from mini_docker.utils import check_root, ensure_directories, get_overlay_paths


class ContainerError(Exception):
    """Exception raised for container operations."""

    pass


class Container:
    """
    Container manager class.

    This class handles the complete lifecycle of a container:
    - Creation with proper isolation
    - Starting and running processes
    - Stopping and cleanup

    Example:
        container = Container()
        config = container.create("./rootfs", ["/bin/sh"])
        container.start(config.id)
        container.stop(config.id)
        container.remove(config.id)
    """

    def __init__(self):
        self.store = MetadataStore()
        self.pods = PodManager()
        ensure_directories()

    def create(
        self,
        rootfs: str,
        command: List[str],
        name: Optional[str] = None,
        hostname: Optional[str] = None,
        use_overlay: bool = True,
        cpu_quota: Optional[int] = None,
        memory_mb: Optional[int] = None,
        max_pids: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
        workdir: str = "/",
        pod_id: Optional[str] = None,
        rootless: bool = False,
        network: bool = False,
        volumes: Optional[List[Dict[str, str]]] = None,
        uid: Optional[int] = None,
        gid: Optional[int] = None,
        auto_remove: bool = False,
        interactive: bool = False,
        tty: bool = False,
        detach: bool = False,
    ) -> ContainerConfig:
        """
        Create a new container.

        Args:
            rootfs: Path to root filesystem
            command: Command to run in container
            name: Optional container name
            hostname: Container hostname
            use_overlay: Use OverlayFS (True) or chroot (False)
            cpu_quota: CPU quota in microseconds
            memory_mb: Memory limit in MB
            max_pids: Maximum processes
            env: Environment variables
            workdir: Working directory
            pod_id: Pod to join (share namespaces)
            rootless: Run in rootless mode

        Returns:
            ContainerConfig instance
        """
        # Validate rootfs
        rootfs = os.path.abspath(rootfs)
        if not os.path.isdir(rootfs):
            raise ContainerError(f"Rootfs not found: {rootfs}")

        # Create configuration
        from mini_docker.metadata import ResourceLimits

        pod = load_pod_config(pod_id) if pod_id else None
        if pod_id and not pod:
            raise ContainerError(f"Pod not found: {pod_id}")

        namespaces = ["pid", "uts", "mnt", "ipc"]
        if (pod and "net" in pod.shared_namespaces) or (network and not pod_id):
            namespaces.append("net")

        resources = ResourceLimits(
            cpu_quota=cpu_quota,
            memory_mb=memory_mb,
            max_pids=max_pids,
        )

        config = ContainerConfig(
            name=name or "",
            hostname=hostname or "",
            rootfs=rootfs,
            command=command,
            use_overlay=use_overlay,
            resources=resources,
            env=env or {},
            workdir=workdir,
            volumes=volumes or [],
            uid=uid,
            gid=gid,
            pod_id=pod_id,
            rootless=rootless,
            auto_remove=auto_remove,
            detach=detach,
            interactive=interactive,
            tty=tty,
            network_enabled=(network and not pod_id)
            or bool(pod and "net" in pod.shared_namespaces),
            namespaces=namespaces,
        )

        # Set up overlay paths if using overlay
        if use_overlay:
            lower, upper, work, merged = get_overlay_paths(config.id)
            config.overlay_lower = lower
            config.overlay_upper = upper
            config.overlay_work = work
            config.overlay_merged = merged

        # Save configuration
        save_container_config(config)

        return config

    def start(self, container_id: str, attach: Optional[bool] = None) -> int:
        """
        Start a container.

        Args:
            container_id: Container ID

        Returns:
            Container PID
        """
        config = load_container_config(container_id)
        if not config:
            raise ContainerError(f"Container not found: {container_id}")

        if attach is None:
            attach = not config.detach

        if config.status == "running":
            raise ContainerError(f"Container already running: {container_id}")

        # Check permissions
        if not config.rootless and not check_root():
            raise ContainerError(
                "Root privileges required (use --rootless for unprivileged mode)"
            )

        # Create synchronization pipes
        # Parent -> Child
        p2c_r, p2c_w = os.pipe()
        # Child -> Parent
        c2p_r, c2p_w = os.pipe()

        # Fork to create container process
        pid = os.fork()

        if pid == 0:
            os.close(p2c_w)
            os.close(c2p_r)
            # Child process - this becomes the container
            try:
                self._run_container(
                    config,
                    sync_read=p2c_r,
                    sync_write=c2p_w,
                    attach=attach,
                )
            except Exception as e:
                print(f"Container error: {e}", file=sys.stderr)
                os._exit(1)
            os._exit(0)
        else:
            os.close(p2c_r)
            os.close(c2p_w)

            # Parent process

            # Wait for child namespace setup before mutating user mappings or network.
            try:
                ready = os.read(c2p_r, 1)
            except OSError:
                ready = b""

            if ready != b"R":
                os.close(p2c_w)
                os.close(c2p_r)
                try:
                    os.waitpid(pid, 0)
                except OSError:
                    pass
                raise ContainerError("Container failed during early startup")

            # Setup user namespace for rootless
            if config.rootless:
                try:
                    setup_user_namespace(pid)
                except Exception as e:
                    print(
                        f"Warning: Failed to setup user namespace: {e}", file=sys.stderr
                    )

            # Set up networking from parent after the child entered its net namespace.
            if (
                config.network_enabled
                and "net" in config.namespaces
                and not config.rootless
                and not config.pod_id
            ):
                try:
                    network = Network(config.id)
                    veth_host, veth_container, ip = network.setup(pid)

                    config = load_container_config(container_id)
                    if config:
                        config.network.ip = ip
                        config.network.veth_host = veth_host
                        config.network.veth_container = veth_container
                        save_container_config(config)
                except Exception as e:
                    print(f"Warning: Network setup failed: {e}", file=sys.stderr)

            # Signal child to proceed
            try:
                os.write(p2c_w, b"X")
            except OSError:
                pass

            # Update status
            update_container_status(container_id, "running", pid=pid)

            if config.pod_id:
                self.pods.add_container(config.pod_id, config.id)

            os.close(p2c_w)
            os.close(c2p_r)

            return pid

    def _run_container(
        self,
        config: ContainerConfig,
        sync_read: Optional[int] = None,
        sync_write: Optional[int] = None,
        attach: bool = False,
    ) -> None:
        """
        Run the container (called in child process after fork).

        This sets up all isolation and executes the container command.
        """
        # We must make sure the container directory exists before setting up the logger.
        # This is because the logger tries to open a file inside this directory immediately.
        import os
        from mini_docker.metadata import get_container_path
        os.makedirs(get_container_path(config.id), exist_ok=True)

        # Set up logging
        logger = ContainerLogger(config.id)

        try:
            # Open the detached log file BEFORE we chroot, so we have the file descriptor ready.
            detached_log_fd = None
            if not attach:
                detached_log_fd = os.open(
                    logger.log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644
                )

            # Check if joining a pod
            pod_ns_paths = {}
            if config.pod_id:
                pod = load_pod_config(config.pod_id)
                if pod and pod.infra_pid:
                    pod_ns_paths = self.pods.get_shared_ns_paths(config.pod_id)

            # Create namespaces
            namespaces_to_create = [
                ns for ns in config.namespaces if ns not in pod_ns_paths
            ]

            if config.rootless:
                namespaces_to_create.append("user")
                if "mnt" not in namespaces_to_create:
                    namespaces_to_create.append("mnt")

            # Create cgroup before unshare (need to add self to cgroup)
            cgroup = None
            if not config.rootless:
                try:
                    cgroup = Cgroup(config.id)
                    cgroup.set_limits(
                        cpu_quota=config.resources.cpu_quota,
                        memory_mb=config.resources.memory_mb,
                        max_pids=config.resources.max_pids,
                    )
                    cgroup.add_process(os.getpid())
                except Exception as e:
                    logger.write(f"Warning: Cgroup setup failed: {e}\n")

            # Enter pod namespaces if specified
            for ns_type, ns_path in pod_ns_paths.items():
                try:
                    fd = os.open(ns_path, os.O_RDONLY)
                    from mini_docker.namespaces import NAMESPACE_FLAGS, setns

                    setns(fd, NAMESPACE_FLAGS.get(ns_type, 0))
                    os.close(fd)
                except Exception as e:
                    logger.write(
                        f"Warning: Failed to enter pod namespace {ns_type}: {e}\n"
                    )

            # Create new namespaces
            if namespaces_to_create:
                create_namespaces(
                    namespaces_to_create,
                    hostname=config.hostname,
                    rootless=config.rootless,
                )

            # Signal parent we have finished namespace setup.
            if sync_write is not None:
                try:
                    os.write(sync_write, b"R")
                except OSError:
                    pass

            # Wait for parent to finish user mapping and network setup.
            if sync_read is not None:
                try:
                    proceed = os.read(sync_read, 1)
                except OSError:
                    proceed = b""

                # Cleanup pipes
                os.close(sync_read)
                if sync_write is not None:
                    os.close(sync_write)

                if proceed != b"X":
                    raise ContainerError("Parent process failed during startup")

            refreshed_config = load_container_config(config.id)
            if refreshed_config:
                config = refreshed_config

            # Set up filesystem
            rootfs_to_pivot = config.rootfs

            if config.use_overlay:
                try:
                    from mini_docker.filesystem import FilesystemError

                    # Set up overlay filesystem
                    lower, upper, work, merged = setup_overlay_filesystem(
                        config.rootfs, config.id
                    )
                    rootfs_to_pivot = merged
                    config.overlay_merged = merged
                except (OSError, FilesystemError) as e:
                    # In rootless mode, overlay might fail. Fallback to chroot.
                    logger.write(
                        f"Warning: Overlayfs setup failed ({e}), falling back to direct chroot\n"
                    )
                    config.use_overlay = False
                    rootfs_to_pivot = config.rootfs

            # Set up minimal /dev
            try:
                setup_minimal_dev(rootfs_to_pivot)
            except Exception:
                pass

            # Use pivot_root for better isolation, fallback to chroot
            try:
                setup_pivot_root(rootfs_to_pivot)
            except Exception as e:
                # Fallback to chroot
                setup_chroot_filesystem(rootfs_to_pivot)

            # Configure network inside container
            if (
                config.network_enabled
                and "net" in config.namespaces
                and config.network.ip
            ):
                try:
                    configure_container_network(config.network.ip)
                except Exception as e:
                    logger.write(f"Warning: Network config failed: {e}\n")

            # Change to working directory
            try:
                os.chdir(config.workdir)
            except OSError:
                os.chdir("/")

            # Set up environment
            os.environ.clear()
            os.environ["PATH"] = (
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            )
            os.environ["HOME"] = "/root"
            os.environ["TERM"] = "xterm"
            os.environ["HOSTNAME"] = config.hostname
            for key, value in config.env.items():
                os.environ[key] = value

            # Apply capabilities
            if not config.rootless:
                try:
                    caps = Capabilities()
                    caps.apply()
                except Exception as e:
                    logger.write(f"Warning: Capability setup failed: {e}\n")

            # Apply seccomp filter
            if config.seccomp_enabled:
                try:
                    seccomp = Seccomp()
                    seccomp.apply()
                except Exception as e:
                    logger.write(f"Warning: Seccomp setup failed: {e}\n")

            if config.gid is not None:
                os.setgid(config.gid)
            if config.uid is not None:
                os.setuid(config.uid)

            # Execute command
            logger.write(f"Starting: {' '.join(config.command)}\n")
            logger.close()

            if not attach:
                if detached_log_fd is not None:
                    os.dup2(detached_log_fd, sys.stdout.fileno())
                    os.dup2(detached_log_fd, sys.stderr.fileno())
                    os.close(detached_log_fd)

                if not config.interactive:
                    devnull_fd = os.open(os.devnull, os.O_RDONLY)
                    os.dup2(devnull_fd, sys.stdin.fileno())
                    os.close(devnull_fd)

            # Replace process with command
            os.execvp(config.command[0], config.command)

        except Exception as e:
            logger.write(f"Container setup failed: {e}\n")
            logger.close()
            raise

    def stop(self, container_id: str, timeout: int = 10) -> bool:
        """
        Stop a running container.

        Args:
            container_id: Container ID
            timeout: Seconds to wait before SIGKILL

        Returns:
            True if stopped successfully
        """
        config = load_container_config(container_id)
        if not config:
            raise ContainerError(f"Container not found: {container_id}")

        if config.status != "running":
            return True

        if not config.pid:
            update_container_status(container_id, "stopped")
            return True

        try:
            # Check if process exists before sending signal
            try:
                os.kill(config.pid, 0)  # Check if process exists
            except OSError as e:
                if e.errno == errno.ESRCH:  # No such process
                    update_container_status(container_id, "stopped")
                    return True
                raise

            # Send SIGTERM
            os.kill(config.pid, signal.SIGTERM)

            # Wait for process to exit
            import time

            for _ in range(timeout * 10):
                try:
                    os.kill(config.pid, 0)
                    time.sleep(0.1)
                except OSError as e:
                    if e.errno == errno.ESRCH:
                        break
                    raise
            else:
                # Process still running, send SIGKILL
                try:
                    os.kill(config.pid, signal.SIGKILL)
                except OSError as e:
                    if e.errno != errno.ESRCH:
                        raise

            # Proper waitpid handling with WNOHANG loop
            exit_code = 0
            try:
                # Try to reap the zombie process
                for _ in range(10):
                    pid, status = os.waitpid(config.pid, os.WNOHANG)
                    if pid != 0:
                        exit_code = (
                            os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
                        )
                        break
                    time.sleep(0.1)
            except ChildProcessError:
                # Process was already reaped by init or doesn't exist
                pass
            except OSError as e:
                if e.errno != errno.ECHILD:
                    raise

            update_container_status(container_id, "stopped", exit_code=exit_code)

            return True

        except OSError as e:
            if e.errno == errno.ESRCH:  # No such process
                update_container_status(container_id, "stopped")
                return True
            raise ContainerError(f"Failed to stop container: {e}")

    def remove(
        self, container_id: str, force: bool = False, remove_volumes: bool = False
    ) -> bool:
        """
        Remove a container.

        Args:
            container_id: Container ID
            force: Force removal of running container
            remove_volumes: Also remove associated volumes

        Returns:
            True if removed successfully
        """
        config = load_container_config(container_id)
        if not config:
            raise ContainerError(f"Container not found: {container_id}")

        if config.status == "running":
            if force:
                self.stop(container_id, timeout=5)
            else:
                raise ContainerError(
                    f"Container is running. Stop first or use force=True"
                )

        # Wrap cleanup in try/except to ensure all cleanup attempted
        errors = []

        # Clean up overlay
        if config.use_overlay:
            try:
                cleanup_overlay(config.id)
            except Exception as e:
                errors.append(f"Overlay cleanup: {e}")

        # Clean up cgroup
        from mini_docker.cgroups import MINI_DOCKER_CGROUP

        cgroup_path = os.path.join(MINI_DOCKER_CGROUP, config.id)
        try:
            delete_cgroup(cgroup_path)
        except Exception as e:
            errors.append(f"Cgroup cleanup: {e}")

        # Clean up networking
        try:
            network = Network(config.id)
            network.cleanup()
        except Exception as e:
            errors.append(f"Network cleanup: {e}")

        # Remove from pod if applicable
        if config.pod_id:
            try:
                self.pods.remove_container(config.pod_id, config.id)
            except Exception as e:
                errors.append(f"Pod cleanup: {e}")

        # Clean up volumes if requested
        if remove_volumes:
            # In a full implementation, this would remove bind mounts and volumes
            # For now, we just clean up any temporary volume directories
            pass

        # Delete metadata
        from mini_docker.metadata import delete_container_config

        result = delete_container_config(container_id)

        # Log any errors but don't fail
        if errors:
            for err in errors:
                print(f"Warning during cleanup: {err}", file=sys.stderr)

        return result

    def exec(
        self,
        container_id: str,
        command: List[str],
        workdir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        uid: Optional[int] = None,
        gid: Optional[int] = None,
        interactive: bool = False,
        tty: bool = False,
    ) -> int:
        """
        Execute a command in a running container.

        Args:
            container_id: Container ID
            command: Command to execute
            workdir: Working directory
            env: Additional environment variables
            uid: User ID to run as
            gid: Group ID to run as
            interactive: Keep STDIN open
            tty: Allocate pseudo-TTY

        Returns:
            Exit code
        """
        config = load_container_config(container_id)
        if not config:
            raise ContainerError(f"Container not found: {container_id}")

        if config.status != "running" or not config.pid:
            raise ContainerError(f"Container not running: {container_id}")

        # Fork to create exec process
        pid = os.fork()

        if pid == 0:
            # Child - enter container namespaces and exec
            try:
                # Enter all container namespaces
                enter_all_namespaces(config.pid)

                # Change to container root
                if config.use_overlay and config.overlay_merged:
                    os.chroot(config.overlay_merged)
                elif config.rootfs:
                    os.chroot(config.rootfs)

                os.chdir(workdir or config.workdir or "/")

                # Set up environment
                os.environ["PATH"] = (
                    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
                )
                for key, value in config.env.items():
                    os.environ[key] = value
                # Add additional environment variables
                if env:
                    for key, value in env.items():
                        os.environ[key] = value

                # Change user if specified
                if gid is not None:
                    os.setgid(gid)
                if uid is not None:
                    os.setuid(uid)

                # Execute
                os.execvp(command[0], command)

            except Exception as e:
                print(f"Exec error: {e}", file=sys.stderr)
                os._exit(1)
        else:
            # Parent - wait for child
            _, status = os.waitpid(pid, 0)
            if os.WIFEXITED(status):
                return os.WEXITSTATUS(status)
            return -1

    def list(self, all_containers: bool = False) -> List[ContainerConfig]:
        """List containers."""
        return self.store.list(all_containers)

    def inspect(self, container_id: str) -> Optional[ContainerConfig]:
        """Get container details."""
        return load_container_config(container_id)

    def logs(
        self,
        container_id: str,
        follow: bool = False,
        tail: Optional[int] = None,
        timestamps: bool = False,
    ) -> None:
        """Print container logs.

        Args:
            container_id: Container ID
            follow: Follow log output
            tail: Number of lines from end
            timestamps: Show timestamps
        """
        from mini_docker.logger import print_logs

        config = load_container_config(container_id)
        if not config:
            raise ContainerError(f"Container not found: {container_id}")

        print_logs(config.id, follow=follow, tail=tail, timestamps=timestamps)
