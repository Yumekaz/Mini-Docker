from mini_docker.container import _command_for_log


def test_command_logs_redact_flags_and_declared_secret_values():
    command = [
        "mongodump",
        "--password=fixture-password",
        "--token",
        "fixture-token",
        "--label",
        "fixture-env-secret",
    ]
    original = command[:]
    logged = _command_for_log(
        command, {"API_SECRET": "fixture-env-secret", "NORMAL": "visible"}
    )
    assert "fixture-password" not in logged
    assert "fixture-token" not in logged
    assert "fixture-env-secret" not in logged
    assert "mongodump" in logged
    assert command == original


def test_normal_command_logging_is_unchanged():
    assert _command_for_log(["httpd", "-p", "80"], {}) == "httpd -p 80"
