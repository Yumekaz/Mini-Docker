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
