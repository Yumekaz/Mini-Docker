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
import json
import os
import signal
import sys
import time
from dataclasses import asdict
from typing import Dict, List, Optional

from mini_docker.capabilities import Capabilities
from mini_docker.cgroups import Cgroup, delete_cgroup
from mini_docker.filesystem import (
    cleanup_overlay,
    setup_chroot_filesystem,
    setup_minimal_dev,
    setup_overlay_filesystem,
    setup_pivot_root,
    mount,
    MS_BIND,
    MS_REC,
    MS_RDONLY,
)
from mini_docker.logger import ContainerLogger
from mini_docker.metadata import (
    ContainerConfig,
    MetadataStore,
    get_container_path,
    load_container_config,
    save_container_config,
    update_container_status,
)
from mini_docker.namespaces import (
    create_namespaces,
    enter_all_namespaces,
    setup_user_namespace,
)
from mini_docker.network import Network, configure_container_network, parse_port_mapping
from mini_docker.pod import PodManager, load_pod_config
from mini_docker.seccomp import Seccomp
from mini_docker.utils import check_root, ensure_directories, get_overlay_paths


class ContainerError(Exception):
    """Base exception for container operations."""


class ContainerNotFoundError(ContainerError):
    """Raised when a referenced container (or related resource) does not exist."""


class ContainerInvalidStateError(ContainerError):
    """Raised when a lifecycle operation is invalid in the current state."""


class ContainerInvalidRequestError(ContainerError):
    """Raised when request parameters are invalid or unsupported."""


class ContainerInternalError(ContainerError):
    """Raised when an unexpected internal runtime failure occurs."""


def _exit_code_from_wait_status(status: int) -> int:
    """Convert a waitpid status into a shell-style process exit code."""
    if hasattr(os, "WIFEXITED") and os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if hasattr(os, "WIFSIGNALED") and os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    if not hasattr(os, "WIFEXITED"):
        return (status >> 8) & 0xFF
    return 1


def _open_container_metadata_fd(container_id: str) -> int:
    """Open the host-side metadata file before chroot/pivot_root."""
    flags = os.O_WRONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    config_path = os.path.join(get_container_path(container_id), "config.json")
    return os.open(config_path, flags)


def _write_config_to_fd(fd: int, config: ContainerConfig) -> None:
    """Persist container metadata through a pre-opened host-side file descriptor."""
    import fcntl
    payload = json.dumps(asdict(config), indent=2).encode("utf-8")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, payload)
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


def _try_reap_process(pid: int) -> Optional[int]:
    """Return a process exit code if this process can reap pid, otherwise None."""
    try:
        reaped_pid, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return None
    except OSError as e:
        if e.errno == errno.ECHILD:
            return None
        raise

    if reaped_pid == 0:
        return None
    return _exit_code_from_wait_status(status)


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
        ports: Optional[List[str]] = None,
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
            raise ContainerInvalidRequestError(f"Rootfs not found: {rootfs}")

        # Create configuration
        from mini_docker.metadata import ResourceLimits

        pod = load_pod_config(pod_id) if pod_id else None
        if pod_id and not pod:
            raise ContainerNotFoundError(f"Pod not found: {pod_id}")

        namespaces = ["pid", "uts", "mnt", "ipc"]
        if (pod and "net" in pod.shared_namespaces) or (
            (network or ports) and not pod_id
        ):
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
            network_enabled=(bool(network or ports) and not pod_id)
            or bool(pod and "net" in pod.shared_namespaces),
            namespaces=namespaces,
        )
        if ports:
            for port_mapping in ports:
                try:
                    parse_port_mapping(port_mapping)
                except Exception as e:
                    raise ContainerInvalidRequestError(str(e)) from e
            config.network.ports = ports

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
            raise ContainerNotFoundError(f"Container not found: {container_id}")

        if attach is None:
            attach = not config.detach

        if config.status == "running":
            raise ContainerInvalidStateError(
                f"Container already running: {container_id}"
            )

        # Check permissions
        if not config.rootless and not check_root():
            raise ContainerInvalidRequestError(
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
                raise ContainerInternalError("Container failed during early startup")

            # Setup user namespace for rootless
            if config.rootless:
                try:
                    setup_user_namespace(pid)
                except Exception as e:
                    # Rootless startup cannot continue safely without user namespace mapping.
                    # Terminate child, reap it to avoid zombies, and keep status non-running.
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except OSError:
                        pass

                    try:
                        os.waitpid(pid, 0)
                    except OSError:
                        pass

                    os.close(p2c_w)
                    os.close(c2p_r)
                    update_container_status(container_id, "stopped", exit_code=1)
                    raise ContainerError(
                        "Rootless startup failed: unable to configure user namespace "
                        f"for pid {pid}: {e}. Verify subordinate ID mappings in "
                        "/etc/subuid and /etc/subgid, and ensure user namespaces "
                        "are enabled in the kernel (CONFIG_USER_NS)."
                    ) from e

            # Set up networking from parent after the child entered its net namespace.
            network_required = config.network_enabled or bool(config.network.ports)
            can_setup_network = (
                "net" in config.namespaces and not config.rootless and not config.pod_id
            )
            network = None
            configured_port_forwards = []

            try:
                if network_required and not can_setup_network:
                    raise ContainerError(
                        "Networking was requested, but this container mode does not support host-managed networking"
                    )

                if can_setup_network:
                    network = Network(config.id)
                    veth_host, veth_container, ip = network.setup(pid)

                    config = load_container_config(container_id)
                    if config:
                        config.network.ip = ip
                        config.network.veth_host = veth_host
                        config.network.veth_container = veth_container
                        save_container_config(config)

                    # Setup port forwarding
                    if config and config.network.ports:
                        from mini_docker.network import setup_port_forwarding

                        for port_mapping in config.network.ports:
                            host_port, container_port = parse_port_mapping(port_mapping)
                            setup_port_forwarding(host_port, container_port, ip)
                            configured_port_forwards.append(
                                (host_port, container_port, ip)
                            )

            except Exception as e:
                if not network_required:
                    print(
                        f"Warning: Optional network setup failed: {e}", file=sys.stderr
                    )
                else:
                    from mini_docker.network import remove_port_forwarding

                    for (
                        host_port,
                        container_port,
                        forward_ip,
                    ) in configured_port_forwards:
                        remove_port_forwarding(host_port, container_port, forward_ip)

                    if network is not None:
                        network.cleanup()

                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass

                    try:
                        _, status = os.waitpid(pid, 0)
                        exit_code = (
                            os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1
                        )
                    except OSError:
                        exit_code = 1

                    update_container_status(
                        container_id, "stopped", exit_code=exit_code
                    )

                    os.close(p2c_w)
                    os.close(c2p_r)
                    raise ContainerError(f"Network setup failed: {e}") from e

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

        detached_log_fd = None
        metadata_fd = None
        try:
            # Open the detached log file BEFORE we chroot, so we have the file descriptor ready.
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
            self._setup_cgroup(config, logger)

            # Enter pod namespaces if specified
            self._enter_pod_namespaces(pod_ns_paths, logger)

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
                    raise ContainerInternalError("Parent process failed during startup")

            refreshed_config = load_container_config(config.id)
            if refreshed_config:
                config = refreshed_config

            needs_pid_supervisor = "pid" in config.namespaces
            if needs_pid_supervisor:
                try:
                    metadata_fd = _open_container_metadata_fd(config.id)
                except OSError as e:
                    raise ContainerInternalError(
                        f"Unable to open host metadata for PID supervisor: {e}"
                    ) from e

                supervisor_metadata_fd = metadata_fd
                workload_log_fd = detached_log_fd
                metadata_fd = None
                detached_log_fd = None
                self._supervise_pid_namespace_workload(
                    config,
                    supervisor_metadata_fd,
                    logger,
                    attach,
                    workload_log_fd,
                )

            workload_log_fd = detached_log_fd
            detached_log_fd = None
            self._prepare_and_exec_workload(
                config,
                logger,
                attach,
                workload_log_fd,
            )

        except Exception as e:
            logger.write(f"Container setup failed: {e}\n")
            logger.close()
            raise
        finally:
            # If startup failed before stdio redirection/exec, make sure this fd
            # doesn't leak in the container supervisor process.
            if detached_log_fd is not None:
                try:
                    os.close(detached_log_fd)
                except OSError:
                    pass
            if metadata_fd is not None:
                try:
                    os.close(metadata_fd)
                except OSError:
                    pass

    def _prepare_and_exec_workload(
        self,
        config: ContainerConfig,
        logger: ContainerLogger,
        attach: bool,
        detached_log_fd: Optional[int],
    ) -> None:
        """Prepare the container root, security policy, stdio, and exec."""
        devnull_fd = None
        try:
            if not attach and not config.interactive:
                devnull_fd = os.open(os.devnull, os.O_RDONLY)

            # Set up filesystem
            rootfs_to_pivot = config.rootfs

            # Configure network inside container (done before pivot_root/chroot so host 'ip' executable is accessible)
            if (
                config.network_enabled
                and "net" in config.namespaces
                and config.network.ip
            ):
                try:
                    configure_container_network(config.network.ip)
                except Exception as e:
                    raise ContainerInternalError(
                        f"Container network configuration failed: {e}"
                    ) from e

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
                    if not config.rootless:
                        raise ContainerInternalError(
                            f"OverlayFS setup failed: {e}"
                        ) from e
                    logger.write(f"Overlayfs setup failed ({e}); using chroot\n")
                    config.use_overlay = False
                    rootfs_to_pivot = config.rootfs

            # Set up minimal /dev
            try:
                setup_minimal_dev(rootfs_to_pivot)
            except Exception:
                pass

            # Mount custom volumes
            for vol in config.volumes:
                host_path = vol.get("host")
                container_path = vol.get("container")
                mode = vol.get("mode", "rw")
                if not host_path or not container_path:
                    continue
                rel_container_path = container_path.lstrip("/")
                target_path = os.path.join(rootfs_to_pivot, rel_container_path)
                os.makedirs(target_path, exist_ok=True)
                flags = MS_BIND | MS_REC
                if mode == "ro":
                    flags |= MS_RDONLY
                try:
                    logger.write(f"Mounting volume: {host_path} -> {target_path} ({mode})\n")
                    mount(host_path, target_path, None, flags)
                except Exception as e:
                    logger.write(f"Warning: Failed to mount volume {host_path} -> {container_path}: {e}\n")

            # Use pivot_root for better isolation, fallback to chroot
            try:
                setup_pivot_root(rootfs_to_pivot)
            except Exception as e:
                # Fallback to chroot
                setup_chroot_filesystem(rootfs_to_pivot)



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

            self._apply_capabilities(config, logger)
            self._apply_seccomp(config, logger)

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
                    os.dup2(devnull_fd, sys.stdin.fileno())
                    os.close(devnull_fd)
                    devnull_fd = None

            self._exec_workload(config)

        except Exception as e:
            logger.write(f"Container setup failed: {e}\n")
            logger.close()
            raise
        finally:
            if devnull_fd is not None:
                try:
                    os.close(devnull_fd)
                except OSError:
                    pass

    def _setup_cgroup(
        self, config: ContainerConfig, logger: ContainerLogger
    ) -> Optional[Cgroup]:
        """Create and assign the process to a cgroup, failing closed in root mode."""
        if config.rootless:
            return None

        try:
            cgroup = Cgroup(config.id)
            cgroup.set_limits(
                cpu_quota=config.resources.cpu_quota,
                memory_mb=config.resources.memory_mb,
                max_pids=config.resources.max_pids,
            )
            cgroup.add_process(os.getpid())
            return cgroup
        except Exception as e:
            logger.write(f"Cgroup setup failed: {e}\n")
            raise ContainerInternalError(f"Cgroup setup failed: {e}") from e

    def _enter_pod_namespaces(
        self, pod_ns_paths: Dict[str, str], logger: ContainerLogger
    ) -> None:
        """Enter pod namespaces and fail startup if any requested namespace fails."""
        for ns_type, ns_path in pod_ns_paths.items():
            fd = None
            try:
                fd = os.open(ns_path, os.O_RDONLY)
                from mini_docker.namespaces import NAMESPACE_FLAGS, setns

                setns(fd, NAMESPACE_FLAGS.get(ns_type, 0))
            except Exception as e:
                logger.write(f"Failed to enter pod namespace {ns_type}: {e}\n")
                raise ContainerInternalError(
                    f"Failed to enter pod namespace {ns_type}: {e}"
                ) from e
            finally:
                if fd is not None:
                    os.close(fd)

    def _apply_capabilities(
        self, config: ContainerConfig, logger: ContainerLogger
    ) -> None:
        """Apply Linux capability policy, failing closed in root mode."""
        if config.rootless:
            return

        try:
            caps = Capabilities()
            caps.apply()
        except Exception as e:
            logger.write(f"Capability setup failed: {e}\n")
            raise ContainerInternalError(f"Capability setup failed: {e}") from e

    def _apply_seccomp(self, config: ContainerConfig, logger: ContainerLogger) -> None:
        """Apply seccomp policy when enabled, failing closed on filter errors."""
        if not config.seccomp_enabled:
            return

        try:
            seccomp = Seccomp()
            seccomp.apply()
        except Exception as e:
            logger.write(f"Seccomp setup failed: {e}\n")
            raise ContainerInternalError(f"Seccomp setup failed: {e}") from e

    def _exec_workload(self, config: ContainerConfig) -> None:
        """Replace the current process with the configured workload."""
        os.execvp(config.command[0], config.command)

    def _supervise_pid_namespace_workload(
        self,
        config: ContainerConfig,
        metadata_fd: Optional[int],
        logger: ContainerLogger,
        attach: bool,
        detached_log_fd: Optional[int],
    ) -> None:
        """
        Fork the actual workload after CLONE_NEWPID setup.

        The process that calls unshare(CLONE_NEWPID) remains in its old PID
        namespace; its next child becomes PID 1 inside the new namespace. This
        supervisor keeps the namespace init reaped and forwards termination
        signals to the workload.
        """
        if metadata_fd is None:
            raise ContainerInternalError("PID supervisor missing metadata fd")

        workload_pid = os.fork()

        if workload_pid == 0:
            try:
                os.close(metadata_fd)
            except OSError:
                pass
            self._prepare_and_exec_workload(
                config,
                logger,
                attach,
                detached_log_fd,
            )
            os._exit(127)

        logger.close()
        if detached_log_fd is not None:
            try:
                os.close(detached_log_fd)
            except OSError:
                pass

        config.pid = workload_pid
        config.status = "running"
        if config.started_at is None:
            config.started_at = time.time()
        _write_config_to_fd(metadata_fd, config)

        def forward_signal(signum, _frame):
            try:
                os.kill(workload_pid, signum)
            except OSError:
                pass

        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                signal.signal(signum, forward_signal)
            except (ValueError, OSError):
                pass

        _, status = os.waitpid(workload_pid, 0)
        exit_code = _exit_code_from_wait_status(status)

        config.status = "stopped"
        config.pid = None
        config.finished_at = time.time()
        config.exit_code = exit_code
        _write_config_to_fd(metadata_fd, config)
        os.close(metadata_fd)

        os._exit(exit_code)

    def restart(self, container_id: str, timeout: int = 10) -> int:
        """
        Restart a container.

        Args:
            container_id: Container ID
            timeout: Seconds to wait for graceful stop before SIGKILL

        Returns:
            New Container PID
        """
        config = load_container_config(container_id)
        if not config:
            raise ContainerNotFoundError(f"Container not found: {container_id}")

        if config.status == "running":
            self.stop(container_id, timeout=timeout)

        # Reset networking runtime metadata before a fresh start so networking
        # setup behaves like a full lifecycle reboot.
        refreshed_config = load_container_config(container_id)
        if refreshed_config and refreshed_config.network:
            self._clear_network_runtime_metadata(refreshed_config)
            save_container_config(refreshed_config)

        # We start it detached by default when restarting
        return self.start(container_id, attach=False)

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
            raise ContainerNotFoundError(f"Container not found: {container_id}")

        if config.status != "running":
            raise ContainerInvalidStateError(f"Container not running: {container_id}")

        if not config.pid:
            return self._finalize_stopped_container(container_id, config)

        try:
            # Check if process exists before sending signal
            try:
                os.kill(config.pid, 0)  # Check if process exists
            except OSError as e:
                if e.errno == errno.ESRCH:  # No such process
                    return self._finalize_stopped_container(
                        container_id, config, exit_code=0
                    )
                raise

            # Send SIGTERM
            os.kill(config.pid, signal.SIGTERM)

            # Wait for process to exit. Reap direct children during the grace
            # window; kill(pid, 0) remains true for zombies.
            import time

            exit_code = None
            for _ in range(max(timeout, 0) * 10):
                exit_code = _try_reap_process(config.pid)
                if exit_code is not None:
                    break

                try:
                    os.kill(config.pid, 0)
                except OSError as e:
                    if e.errno == errno.ESRCH:
                        exit_code = 0
                        break
                    raise

                time.sleep(0.1)

            if exit_code is None:
                # Process still running, send SIGKILL
                try:
                    os.kill(config.pid, signal.SIGKILL)
                except OSError as e:
                    if e.errno != errno.ESRCH:
                        raise
                    exit_code = 0

                for _ in range(10):
                    reaped_exit_code = _try_reap_process(config.pid)
                    if reaped_exit_code is not None:
                        exit_code = reaped_exit_code
                        break
                    time.sleep(0.1)

                if exit_code is None:
                    exit_code = 128 + signal.SIGKILL

            return self._finalize_stopped_container(
                container_id, config, exit_code=exit_code
            )

        except OSError as e:
            if e.errno == errno.ESRCH:  # No such process
                return self._finalize_stopped_container(
                    container_id, config, exit_code=0
                )
            raise ContainerInternalError(f"Failed to stop container: {e}")

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
            raise ContainerNotFoundError(f"Container not found: {container_id}")

        if config.status == "running":
            if force:
                self.stop(container_id, timeout=5)
            else:
                raise ContainerInvalidStateError(
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

        errors.extend(self._cleanup_runtime_resources(config))

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
            raise ContainerNotFoundError(f"Container not found: {container_id}")

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
            raise ContainerNotFoundError(f"Container not found: {container_id}")

        print_logs(config.id, follow=follow, tail=tail, timestamps=timestamps)

    def _clear_network_runtime_metadata(self, config: ContainerConfig) -> None:
        """Clear host-assigned network values that are valid for one start only."""
        config.network.ip = None
        config.network.veth_host = None
        config.network.veth_container = None

    def _finalize_stopped_container(
        self,
        container_id: str,
        config: ContainerConfig,
        exit_code: Optional[int] = None,
    ) -> bool:
        """Persist stopped state and release one-start runtime resources."""
        update_container_status(container_id, "stopped", exit_code=exit_code)

        cleanup_errors = self._cleanup_runtime_resources(config)
        if not any(
            error.startswith(("Port forwarding cleanup", "Network cleanup"))
            for error in cleanup_errors
        ):
            refreshed_config = load_container_config(container_id)
            if refreshed_config and refreshed_config.network:
                self._clear_network_runtime_metadata(refreshed_config)
                save_container_config(refreshed_config)

        if cleanup_errors:
            raise ContainerInternalError(
                "Container stopped, but runtime cleanup failed: "
                + "; ".join(cleanup_errors)
            )

        return True

    def _cleanup_runtime_resources(self, config: ContainerConfig) -> List[str]:
        """Clean up host-side runtime resources while preserving container state."""
        errors = []

        if config.network.ports and config.network.ip:
            try:
                from mini_docker.network import remove_port_forwarding

                for port_mapping in config.network.ports:
                    host_port, container_port = parse_port_mapping(port_mapping)
                    remove_port_forwarding(host_port, container_port, config.network.ip)
            except Exception as e:
                errors.append(f"Port forwarding cleanup: {e}")

        if config.network_enabled or config.network.veth_host or config.network.ip:
            try:
                network = Network(config.id)
                network.veth_host = config.network.veth_host
                network.cleanup()
            except Exception as e:
                errors.append(f"Network cleanup: {e}")

        if not config.rootless:
            from mini_docker.cgroups import MINI_DOCKER_CGROUP

            cgroup_path = os.path.join(MINI_DOCKER_CGROUP, config.id)
            try:
                delete_cgroup(cgroup_path)
                if os.path.exists(cgroup_path):
                    errors.append(f"Cgroup cleanup: cgroup still exists: {cgroup_path}")
            except Exception as e:
                errors.append(f"Cgroup cleanup: {e}")

        return errors
