"""Pure-Python logic tests for Mini-Docker."""

import pytest


@pytest.fixture
def isolated_storage(tmp_path, monkeypatch):
    root = tmp_path / "mini-docker"
    containers = root / "containers"
    images = root / "images"
    overlay = root / "overlay"
    pods = root / "pods"
    run = tmp_path / "run"

    import mini_docker.image_builder as image_builder
    import mini_docker.metadata as metadata
    import mini_docker.pod as pod
    import mini_docker.utils as utils

    monkeypatch.setattr(utils, "MINI_DOCKER_ROOT", str(root))
    monkeypatch.setattr(utils, "CONTAINERS_PATH", str(containers))
    monkeypatch.setattr(utils, "IMAGES_PATH", str(images))
    monkeypatch.setattr(utils, "OVERLAY_PATH", str(overlay))
    monkeypatch.setattr(utils, "PODS_PATH", str(pods))
    monkeypatch.setattr(utils, "RUN_PATH", str(run))

    monkeypatch.setattr(metadata, "CONTAINERS_PATH", str(containers))
    monkeypatch.setattr(pod, "PODS_PATH", str(pods))
    monkeypatch.setattr(image_builder, "IMAGES_PATH", str(images))

    utils.ensure_directories()

    return {
        "root": root,
        "containers": containers,
        "images": images,
        "overlay": overlay,
        "pods": pods,
        "run": run,
    }


def test_container_lookup_by_prefix_and_name(isolated_storage):
    from mini_docker.metadata import (
        ContainerConfig,
        find_container_id,
        save_container_config,
    )

    alpha = ContainerConfig(
        id="a" * 12,
        name="alpha",
        rootfs="/rootfs",
        command=["/bin/sh"],
    )
    beta = ContainerConfig(
        id="b" * 12,
        name="beta",
        rootfs="/rootfs",
        command=["/bin/sleep", "1"],
    )

    save_container_config(alpha)
    save_container_config(beta)

    assert find_container_id("aaaa") == alpha.id
    assert find_container_id("beta") == beta.id


def test_container_load_refreshes_stale_running_state(isolated_storage, monkeypatch):
    import mini_docker.metadata as metadata

    config = metadata.ContainerConfig(
        id="c" * 12,
        name="worker",
        rootfs="/rootfs",
        command=["/bin/sleep", "60"],
        status="running",
        pid=4242,
    )
    metadata.save_container_config(config)

    monkeypatch.setattr(metadata, "is_process_alive", lambda pid: False)

    loaded = metadata.load_container_config("worker")

    assert loaded is not None
    assert loaded.status == "stopped"
    assert loaded.pid is None
    assert loaded.finished_at is not None


def test_pod_lookup_and_stale_infra_refresh(isolated_storage, monkeypatch):
    import mini_docker.pod as pod

    config = pod.PodConfig(
        id="p" * 12,
        name="api-pod",
        infra_pid=5151,
        status="running",
        shared_namespaces=["net", "ipc", "uts"],
    )
    pod.save_pod_config(config)

    assert pod.find_pod_id("api-pod") == config.id

    monkeypatch.setattr(pod, "is_process_alive", lambda pid: False)

    loaded = pod.load_pod_config("api-pod")

    assert loaded is not None
    assert loaded.status == "stopped"
    assert loaded.infra_pid is None


def test_parse_cpu_limit_percent_converts_percent_to_quota():
    from mini_docker.cli import parse_cpu_limit_percent

    assert parse_cpu_limit_percent(50) == 50000
    assert parse_cpu_limit_percent(1) == 1000
    assert parse_cpu_limit_percent(None) is None

    with pytest.raises(ValueError):
        parse_cpu_limit_percent(0)

    with pytest.raises(ValueError):
        parse_cpu_limit_percent(101)


def test_parse_image_file_handles_comments_and_continuations():
    from mini_docker.image_builder import parse_image_file

    content = """
    # comment
    FROM ./rootfs
    RUN echo hello \\
        world
    CMD ["/bin/sh"]
    """

    instructions = parse_image_file(content)

    assert instructions == [
        ("FROM", "./rootfs"),
        ("RUN", "echo hello world"),
        ("CMD", '["/bin/sh"]'),
    ]


def test_entrypoint_and_cmd_are_tracked_independently(isolated_storage):
    from mini_docker.image_builder import ImageBuilder

    builder = ImageBuilder()
    builder._handle_cmd("image", '["python", "app.py"]', "")
    builder._handle_entrypoint("image", '["/mini-docker-entry"]', "")

    assert builder.cmd == ["python", "app.py"]
    assert builder.entrypoint == ["/mini-docker-entry"]
