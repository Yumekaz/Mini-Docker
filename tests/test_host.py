import json
import os

from mini_docker import host


def _make_rootfs(tmp_path, executable: bool):
    rootfs = tmp_path / "rootfs"
    bin_dir = rootfs / "bin"
    bin_dir.mkdir(parents=True)

    busybox = bin_dir / "busybox"
    busybox.write_text("#!/bin/sh\n")
    sh = bin_dir / "sh"
    sh.write_text("#!/bin/sh\n")

    mode = 0o755 if executable else 0o644
    busybox.chmod(mode)
    sh.chmod(mode)

    return rootfs


def test_validate_rootfs_reports_non_executable_busybox(tmp_path):
    rootfs = _make_rootfs(tmp_path, executable=False)

    report = host.validate_rootfs(str(rootfs))

    assert any(
        check["name"] == "rootfs:bin/busybox" and check["status"] == host.FAIL
        for check in report
    )


def test_validate_rootfs_accepts_executable_busybox(tmp_path):
    rootfs = _make_rootfs(tmp_path, executable=True)

    report = host.validate_rootfs(str(rootfs))

    assert not any(check["status"] == host.FAIL for check in report)


def test_rootless_host_report_does_not_require_iptables(tmp_path):
    rootfs = _make_rootfs(tmp_path, executable=True)

    report = host.collect_host_report(rootless=True, rootfs_path=str(rootfs))

    names = {check["name"] for check in report["checks"]}
    assert "command:iptables" not in names


def test_runtime_cleanup_dry_run_does_not_require_storage_dirs(monkeypatch, tmp_path):
    missing_containers = tmp_path / "missing-containers"
    missing_cgroups = tmp_path / "missing-cgroups"
    monkeypatch.setattr(host, "CONTAINERS_PATH", str(missing_containers))
    monkeypatch.setattr(host, "MINI_DOCKER_CGROUP", str(missing_cgroups))

    report = host.cleanup_runtime_resources(dry_run=True)

    assert report["ok"] is True
    assert os.path.exists(missing_containers) is False


def _write_container_config(containers_path, directory_name: str, data: dict):
    container_dir = containers_path / directory_name
    container_dir.mkdir(parents=True)
    (container_dir / "config.json").write_text(json.dumps(data))


def _unexpected_root_call(*args, **kwargs):
    raise AssertionError("root cleanup command should not be called")


def test_runtime_cleanup_refuses_invalid_metadata_container_id(monkeypatch, tmp_path):
    containers = tmp_path / "containers"
    cgroups = tmp_path / "cgroups"
    cgroups.mkdir()

    _write_container_config(
        containers,
        "bad",
        {
            "id": "../../host-path",
            "status": "stopped",
            "network": {
                "ip": "10.0.0.2",
                "ports": ["8080:80"],
                "veth_host": "eth0",
            },
        },
    )
    monkeypatch.setattr(host, "CONTAINERS_PATH", str(containers))
    monkeypatch.setattr(host, "MINI_DOCKER_CGROUP", str(cgroups))
    monkeypatch.setattr(host, "delete_veth", _unexpected_root_call)
    monkeypatch.setattr(host, "remove_port_forwarding", _unexpected_root_call)
    monkeypatch.setattr(host, "remove_nat", _unexpected_root_call)
    monkeypatch.setattr(host, "delete_bridge", _unexpected_root_call)

    report = host.cleanup_runtime_resources(dry_run=False)

    assert report["ok"] is False
    assert any(
        op["action"] == "skip-container"
        and op["target"] == "../../host-path"
        and op["status"] == host.FAIL
        for op in report["operations"]
    )
    assert any(
        op["action"] == "skip-network-base" and op["status"] == host.SKIP
        for op in report["operations"]
    )


def test_runtime_cleanup_refuses_unowned_veth_name(monkeypatch, tmp_path):
    container_id = "a1b2c3d4e5f6"
    containers = tmp_path / "containers"
    cgroups = tmp_path / "cgroups"
    cgroups.mkdir()

    _write_container_config(
        containers,
        container_id,
        {
            "id": container_id,
            "status": "stopped",
            "network": {"veth_host": "eth0"},
        },
    )
    monkeypatch.setattr(host, "CONTAINERS_PATH", str(containers))
    monkeypatch.setattr(host, "MINI_DOCKER_CGROUP", str(cgroups))
    monkeypatch.setattr(host, "delete_veth", _unexpected_root_call)
    monkeypatch.setattr(host, "remove_nat", _unexpected_root_call)
    monkeypatch.setattr(host, "delete_bridge", _unexpected_root_call)

    report = host.cleanup_runtime_resources(dry_run=False)

    assert report["ok"] is False
    assert any(
        op["action"] == "cleanup-veth"
        and op["target"] == "eth0"
        and op["status"] == host.FAIL
        for op in report["operations"]
    )
    assert any(
        op["action"] == "skip-network-base" and op["status"] == host.SKIP
        for op in report["operations"]
    )


def test_runtime_cleanup_refuses_port_cleanup_outside_bridge_subnet(
    monkeypatch, tmp_path
):
    container_id = "abcdef123456"
    containers = tmp_path / "containers"
    cgroups = tmp_path / "cgroups"
    cgroups.mkdir()

    _write_container_config(
        containers,
        container_id,
        {
            "id": container_id,
            "status": "stopped",
            "network": {
                "ip": "192.168.1.10",
                "ports": ["8080:80"],
            },
        },
    )
    monkeypatch.setattr(host, "CONTAINERS_PATH", str(containers))
    monkeypatch.setattr(host, "MINI_DOCKER_CGROUP", str(cgroups))
    monkeypatch.setattr(host, "remove_port_forwarding", _unexpected_root_call)
    monkeypatch.setattr(host, "remove_nat", _unexpected_root_call)
    monkeypatch.setattr(host, "delete_bridge", _unexpected_root_call)

    report = host.cleanup_runtime_resources(dry_run=False)

    assert report["ok"] is False
    assert any(
        op["action"] == "cleanup-port"
        and op["target"] == "192.168.1.10"
        and op["status"] == host.FAIL
        for op in report["operations"]
    )
    assert any(
        op["action"] == "skip-network-base" and op["status"] == host.SKIP
        for op in report["operations"]
    )


def test_cleanup_empty_cgroup_refuses_path_outside_runtime_root(monkeypatch, tmp_path):
    cgroup_root = tmp_path / "mini-docker-cgroups"
    outside_cgroup = tmp_path / "outside" / "abcdef123456"
    outside_cgroup.mkdir(parents=True)
    (outside_cgroup / "cgroup.procs").write_text("")
    monkeypatch.setattr(host, "MINI_DOCKER_CGROUP", str(cgroup_root))

    operation = host._cleanup_empty_cgroup(str(outside_cgroup), dry_run=False)

    assert operation["status"] == host.FAIL
    assert outside_cgroup.exists()
