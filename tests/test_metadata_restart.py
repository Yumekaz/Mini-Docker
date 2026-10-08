from types import SimpleNamespace

from mini_docker import metadata


def test_running_generation_clears_previous_completion_metadata(monkeypatch):
    config = SimpleNamespace(
        status="stopped",
        pid=None,
        supervisor_pid=None,
        started_at=10.0,
        finished_at=11.0,
        exit_code=137,
    )
    saved = []
    monkeypatch.setattr(metadata, "find_container_id", lambda identifier: "fixture")
    monkeypatch.setattr(
        metadata, "_load_container_config_by_id", lambda *args, **kwargs: config
    )
    monkeypatch.setattr(metadata, "save_container_config", saved.append)
    monkeypatch.setattr(metadata.time, "time", lambda: 20.0)
    assert metadata.update_container_status("fixture", "running", pid=123)
    assert saved == [config]
    assert config.started_at == 20.0
    assert config.finished_at is None
    assert config.exit_code is None
