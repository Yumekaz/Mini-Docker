import json
import os
import shutil
import subprocess
import sys


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_executable(path, content):
    path.write_text(content)
    path.chmod(0o755)


def _fake_python_script():
    return f"""#!{sys.executable}
import json
import os
import sys

args = sys.argv[1:]
log_path = os.environ["FAKE_MINI_DOCKER_LOG"]

record = {{
    "args": args,
    "mini_docker_root": os.environ.get("MINI_DOCKER_ROOT"),
    "mini_docker_run": os.environ.get("MINI_DOCKER_RUN"),
}}

with open(log_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")

if args[:2] != ["-m", "mini_docker"]:
    print(f"unexpected python invocation: {{args}}", file=sys.stderr)
    sys.exit(90)

command = args[2] if len(args) > 2 else ""

if command == "doctor":
    sys.exit(int(os.environ.get("FAKE_DOCTOR_STATUS", "0")))
elif command == "run":
    if os.environ.get("FAKE_RUN_WITHOUT_CONTAINER_ID") == "1":
        print("mini-docker-smoke")
    else:
        print("Created container: smoke123")
        print("mini-docker-smoke")
elif command == "ps":
    print('[{{"id": "smoke123", "status": "stopped"}}]')
elif command == "logs":
    print("mini-docker-smoke")
elif command == "rm":
    print("Removed smoke123")
elif command == "cleanup":
    print("dry-run cleanup ok")
else:
    print(f"unexpected mini-docker command: {{command}}", file=sys.stderr)
    sys.exit(91)
"""


def _fake_id_script():
    return """#!/usr/bin/env sh
if [ "$1" = "-u" ]; then
    echo 1000
    exit 0
fi
echo "unsupported fake id invocation" >&2
exit 64
"""


def _make_fake_bin(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "python3", _fake_python_script())
    _write_executable(fake_bin / "id", _fake_id_script())
    return fake_bin


def _make_rootfs(tmp_path):
    rootfs = tmp_path / "rootfs"
    (rootfs / "bin").mkdir(parents=True)
    return rootfs


def _run_smoke(tmp_path, *args, extra_env=None):
    log_path = tmp_path / "mini-docker-calls.jsonl"
    fake_bin = _make_fake_bin(tmp_path)
    env = os.environ.copy()
    env.update(
        {
            "FAKE_MINI_DOCKER_LOG": str(log_path),
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        }
    )
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        ["bash", "scripts/runtime-smoke.sh", *args],
        cwd=_repo_root(),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    records = []
    if log_path.exists():
        records = [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
        ]
    return result, records


def test_runtime_smoke_defaults_to_rootless_isolated_lifecycle(tmp_path):
    rootfs = _make_rootfs(tmp_path)

    result, records = _run_smoke(
        tmp_path,
        "--rootfs",
        str(rootfs),
        extra_env={"FAKE_DOCTOR_STATUS": "2"},
    )

    assert result.returncode == 0, result.stderr
    assert "runtime smoke passed (rootless)" in result.stdout

    state_roots = {record["mini_docker_root"] for record in records}
    run_roots = {record["mini_docker_run"] for record in records}
    assert len(state_roots) == 1
    assert len(run_roots) == 1

    state_root = next(iter(state_roots))
    run_root = next(iter(run_roots))
    state_dir = os.path.dirname(state_root)
    copied_rootfs = os.path.join(state_dir, "rootfs")

    assert copied_rootfs != str(rootfs)

    commands = [record["args"][2:] for record in records]
    assert commands == [
        ["doctor", "--rootless", "--rootfs", copied_rootfs],
        [
            "run",
            "--rootless",
            "--no-overlay",
            copied_rootfs,
            "/bin/echo",
            "mini-docker-smoke",
        ],
        ["ps", "-a", "--format", "json"],
        ["logs", "smoke123"],
        ["rm", "smoke123"],
        ["cleanup", "--runtime", "--dry-run", "--force"],
    ]

    assert state_root.startswith("/tmp/mini-docker-smoke.")
    assert run_root.startswith("/tmp/mini-docker-smoke.")
    assert not os.path.exists(state_dir)


def test_runtime_smoke_refuses_root_mode_for_non_root_user(tmp_path):
    rootfs = _make_rootfs(tmp_path)

    result, records = _run_smoke(tmp_path, "--root", "--rootfs", str(rootfs))

    assert result.returncode == 1
    assert "Root smoke mode requires root" in result.stderr
    assert records == []


def test_runtime_smoke_keep_state_preserves_isolated_state(tmp_path):
    rootfs = _make_rootfs(tmp_path)

    result, records = _run_smoke(tmp_path, "--rootfs", str(rootfs), "--keep-state")

    assert result.returncode == 0, result.stderr
    state_root = records[0]["mini_docker_root"]
    state_dir = os.path.dirname(state_root)
    copied_rootfs = os.path.join(state_dir, "rootfs")
    assert f"Keeping smoke state at: {state_dir}" in result.stdout
    assert os.path.isdir(state_dir)
    assert os.path.isdir(copied_rootfs)

    shutil.rmtree(state_dir)


def test_runtime_smoke_fails_when_run_output_has_no_container_id(tmp_path):
    rootfs = _make_rootfs(tmp_path)

    result, records = _run_smoke(
        tmp_path,
        "--rootfs",
        str(rootfs),
        extra_env={"FAKE_RUN_WITHOUT_CONTAINER_ID": "1"},
    )

    assert result.returncode == 1
    assert "Unable to find container id in run output" in result.stderr
    assert [record["args"][2] for record in records] == ["doctor", "run"]
