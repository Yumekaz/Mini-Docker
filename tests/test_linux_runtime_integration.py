import os
import re
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Mini-Docker runtime integration requires Linux"
)


_PROC_MASK_TARGETS = ("sysrq-trigger", "kcore", "keys", "timer_list")

# Run inside both a freshly started workload and its exec child. Inspect mounts
# before opening a mask O_RDONLY; never probe kernel controls with write opens.
_PROC_PROTECTION_INSPECTION = r"""
print('proc-probe-entered', flush=True)
import errno, os, re, stat, sys

def unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)

mounts = {}
with open('/proc/self/mountinfo') as stream:
    for line in stream:
        fields = line.split()
        separator = fields.index('-')
        target = unescape(fields[4])
        if target == '/dev' or target == '/proc' or target.startswith('/proc/'):
            assert target not in mounts, ('stacked protected mount', target)
        mounts[target] = (unescape(fields[3]), set(fields[5].split(',')),
                          fields[separator + 1], fields[2])

proc = mounts['/proc']
assert proc[0] == '/' and proc[2] == 'proc', proc
assert {'ro', 'nodev', 'nosuid', 'noexec'} <= proc[1], proc
for target, entry in mounts.items():
    if target.startswith('/proc/'):
        assert 'ro' in entry[1], (target, entry)
if sys.argv[1] != '-':
    assert os.readlink('/proc/self/ns/mnt') == sys.argv[1], 'exec namespace differs'

dev = mounts['/dev']
assert dev[2] == 'tmpfs' and 'ro' in dev[1], dev
source_fd = os.open('/dev/.proc-mask', os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
try:
    source = os.fstat(source_fd)
    assert stat.S_ISREG(source.st_mode), source
    assert stat.S_IMODE(source.st_mode) == 0o444 and source.st_size == 0, source
    assert os.read(source_fd, 1) == b'', 'mask source is not empty'
finally:
    os.close(source_fd)

expected = set(sys.argv[2:])
targets = {'sysrq-trigger', 'kcore', 'keys', 'timer_list'}
assert expected <= targets, expected
for name in sorted(targets):
    path = '/proc/' + name
    if name not in expected:
        try:
            os.lstat(path)
        except OSError as error:
            assert error.errno == errno.ENOENT, (path, error)
        else:
            raise AssertionError(('unexpected proc feature', path))
        assert path not in mounts, ('absent feature has a mount', path)
        continue
    entry = mounts[path]
    assert entry[0] == '/.proc-mask' and entry[2] == 'tmpfs', (path, entry)
    assert 'ro' in entry[1] and entry[3] == dev[3], (path, entry, dev)
    # Only after verifying an inert tmpfs mask mount may content be inspected.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        assert stat.S_ISREG(info.st_mode), (path, info)
        assert stat.S_IMODE(info.st_mode) == 0o444 and info.st_size == 0, (path, info)
        assert (info.st_dev, info.st_ino) == (source.st_dev, source.st_ino), path
        assert os.read(fd, 1) == b'', ('mask is not empty', path)
    finally:
        os.close(fd)
print('proc-protection-inspected', flush=True)
"""


def _available_proc_mask_targets():
    """Require every kernel feature present outside the workload to be masked."""
    present = []
    for name in _PROC_MASK_TARGETS:
        try:
            os.lstat("/proc/" + name)
        except FileNotFoundError:
            continue
        present.append(name)
    return present


def _require_root():
    if os.geteuid() != 0:
        pytest.skip("runtime integration requires root")


def _require_cgroups_v2():
    if not os.path.exists("/sys/fs/cgroup/cgroup.controllers"):
        pytest.skip("runtime integration requires cgroups v2")


def _require_private_mount_namespace():
    if not shutil.which("unshare"):
        pytest.skip("runtime integration requires unshare for private mount cleanup")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rootfs_path():
    rootfs = os.path.join(_repo_root(), "rootfs")
    if not os.path.exists(os.path.join(rootfs, "bin", "sh")):
        pytest.skip("rootfs/bin/sh is required; run scripts/setup.sh first")
    return rootfs


def _copy_rootfs(tmp_path):
    rootfs = tmp_path / "rootfs"
    # The bundled rootfs contains character devices under dev/. copytree would
    # read /dev/random and /dev/urandom as ordinary files, filling tmp_path
    # indefinitely. The runtime creates a minimal disposable /dev itself.
    shutil.copytree(
        _rootfs_path(),
        rootfs,
        symlinks=True,
        ignore=shutil.ignore_patterns("dev"),
    )
    (rootfs / "dev").mkdir()
    return str(rootfs)


def _require_rootfs_binary(rootfs, binary):
    if not os.path.exists(os.path.join(rootfs, "bin", binary)):
        pytest.skip(f"rootfs/bin/{binary} is required for this integration test")


def _install_python_memory_hog(rootfs):
    """Install the host Python runtime into the disposable copied rootfs.

    BusyBox has no allocator workload that reliably exercises cgroup memory
    enforcement. This fixture is isolated to the test copy and never changes
    the bundled rootfs.
    """
    executable = os.path.realpath(sys.executable)
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    stdlib = os.path.join(sys.base_prefix, "lib", version)
    if not os.path.isfile(executable) or not os.path.isdir(stdlib):
        pytest.skip("host Python runtime is unavailable for memory-limit test")

    def copy_absolute(source):
        target = os.path.join(rootfs, source.lstrip("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=True)

    copy_absolute(executable)
    shutil.copytree(stdlib, os.path.join(rootfs, "usr", "lib", version), symlinks=True)
    ldd = subprocess.run(
        ["ldd", executable], capture_output=True, text=True, check=False
    )
    if ldd.returncode != 0:
        pytest.skip("ldd is unavailable for memory-limit test")
    libraries = re.findall(r"(?:=>\s+)?(/\S+)", ldd.stdout)
    for library in libraries:
        if os.path.isfile(library):
            copy_absolute(library)
    return executable


def _runtime_env(tmp_path):
    env = os.environ.copy()
    env["MINI_DOCKER_ROOT"] = str(tmp_path / "state")
    env["MINI_DOCKER_RUN"] = str(tmp_path / "run")
    return env


def _run_root_container(tmp_path, args, **kwargs):
    """Run each privileged case inside an expendable mount namespace.

    A runtime regression must not leave proc/sys/dev mounts under pytest's
    copied rootfs. The namespace disappears when the command exits, matching
    the guarded CI root-smoke workflow.
    """
    _require_private_mount_namespace()
    return subprocess.run(
        [
            "unshare",
            "--mount",
            "--pid",
            "--fork",
            "--kill-child",
            "--mount-proc",
            "--propagation",
            "private",
            "--",
            *args,
        ],
        cwd=_repo_root(),
        env=_runtime_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=30,
        **kwargs,
    )


def test_pid_namespace_workload_runs_as_pid_one(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)

    result = _run_root_container(
        tmp_path,
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            rootfs,
            "--",
            "/bin/sh",
            "-c",
            "echo $$",
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "1" in result.stdout.splitlines()


def test_memory_limit_cgroup_is_enforced(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)
    python = _install_python_memory_hog(rootfs)

    result = _run_root_container(
        tmp_path,
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            "--memory",
            "32M",
            rootfs,
            "--",
            "/bin/sh",
            "-c",
            f"PYTHONHOME=/usr {python} -S -c 'blocks=[]\nwhile True: blocks.append(bytearray(1024 * 1024))'",
        ],
    )

    assert result.returncode != 0


def test_rw_volume_roundtrip_between_host_and_container(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)

    host_dir = tmp_path / "hostvol"
    host_dir.mkdir()
    (host_dir / "seed.txt").write_text("from-host")

    result = _run_root_container(
        tmp_path,
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            "--volume",
            f"{host_dir}:/data:rw",
            rootfs,
            "--",
            "/bin/sh",
            "-c",
            "cat /data/seed.txt && echo from-container > /data/reply.txt",
        ],
    )

    assert result.returncode == 0, result.stderr
    assert "from-host" in result.stdout
    reply = host_dir / "reply.txt"
    assert reply.exists(), "container write to rw volume did not reach the host"
    assert reply.read_text().strip() == "from-container"


def test_ro_volume_rejects_writes(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)

    host_dir = tmp_path / "hostvol-ro"
    host_dir.mkdir()
    (host_dir / "keep.txt").write_text("immutable")

    # Write attempt must fail inside the container...
    write_attempt = _run_root_container(
        tmp_path,
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            "--volume",
            f"{host_dir}:/data:ro",
            rootfs,
            "--",
            "/bin/sh",
            "-c",
            "echo tampered > /data/keep.txt",
        ],
    )
    assert (
        write_attempt.returncode != 0
    ), "write to ro volume succeeded — read-only remount is not enforced"

    # ...and the file content must be untouched on the host.
    assert (host_dir / "keep.txt").read_text() == "immutable"


def test_failed_volume_mount_fails_container_start(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)

    # A bind source that cannot be created/mounted must abort startup instead
    # of silently starting the container without its declared volume.
    bad_host = tmp_path / "not-a-dir" / "file"  # parent is a regular file
    (tmp_path / "not-a-dir").write_text("blocker")

    result = _run_root_container(
        tmp_path,
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            "--volume",
            f"{bad_host}:/data:ro",
            rootfs,
            "--",
            "/bin/sh",
            "-c",
            "echo should-not-run",
        ],
    )
    assert (
        result.returncode != 0
    ), "container started despite a failed volume mount (fail-open behavior)"
    assert "should-not-run" not in result.stdout


def test_verified_pidfd_stop_and_exec_use_owned_container(tmp_path):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)
    python = _install_python_memory_hog(rootfs)
    script = r"""
import os, signal, sys, time, traceback
from mini_docker.container import Container
from mini_docker.metadata import load_container_config
from mini_docker.process_identity import VerifiedProcess, capture_identity
manager=Container()
config=manager.create(sys.argv[1], ['/bin/sleep','30'], detach=True)
stage='start'
primary_failure=False
shell_status=probe_status=stop_result=None
def mark(event, **values):
    print('rootexec-stage ' + event + ' ' + repr(values), flush=True)
mark('start.begin', python=sys.version, pid=os.getpid(), container=config.id)
try:
    manager.start(config.id, attach=False)
    mark('start.exit')
    stage='readiness'
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        running=load_container_config(config.id)
        if running.pid and running.pid != running.supervisor_pid and running.exec_ready:
            break
        time.sleep(.02)
    assert running.status=='running'
    assert running.pid_identity==capture_identity(running.pid)
    assert running.supervisor_identity==capture_identity(running.supervisor_pid)
    mark('readiness.exit', workload=running.pid, supervisor=running.supervisor_pid)
    # An interrupted benign sleep must resume under the unchanged real filter.
    # Observe it blocked in sleep, not merely alive while SIGSYS core handling
    # is pending. /proc may report the original or restarted native sleep call.
    with VerifiedProcess(running.pid, running.pid_identity) as workload:
        def wait_state(expected):
            limit=time.monotonic()+1
            state=syscall=None
            while time.monotonic()<limit:
                assert workload.alive(), ('benign sleep exited', stage)
                with open('/proc/{}/stat'.format(running.pid)) as stream:
                    state=stream.read().rsplit(') ',1)[1].split()[0]
                if state==expected:
                    if expected=='S':
                        with open('/proc/{}/syscall'.format(running.pid)) as stream:
                            syscall=stream.read().split()[0]
                        if syscall not in {'35','219','230'}:
                            time.sleep(.02)
                            continue
                    mark(stage+'.observed', state=state, syscall=syscall)
                    return
                time.sleep(.02)
            raise AssertionError(('sleep state deadline', stage, expected, state, syscall))
        stage='sleep-before-stop'
        wait_state('S')
        stage='sleep-sigstop'
        mark(stage+'.begin')
        workload.send(signal.SIGSTOP)
        wait_state('T')
        stage='sleep-sigcont'
        mark(stage+'.begin')
        workload.send(signal.SIGCONT)
        wait_state('S')
        assert workload.alive()
        assert running.pid_identity==capture_identity(running.pid)
        mark(stage+'.exit', alive=True)
    stage='shell-exec'
    mark(stage+'.begin', expected_denial='mknodat')
    assert (shell_status := manager.exec(config.id,['/bin/sh','-c',
        'set -e; test $$ -gt 1; '
        '/bin/busybox grep -q "^Seccomp:[[:space:]]*2$" /proc/self/status; '
        '/bin/busybox grep -q "^NoNewPrivs:[[:space:]]*1$" /proc/self/status; '
        'test ! -e /dev/console; '
        'if /bin/busybox mknod /dev/exec-forbidden b 7 0; then exit 41; fi; '
        'test ! -e /dev/exec-forbidden || exit 42; '
        'for key in CapEff CapPrm CapBnd; do '
        'value=$(/bin/busybox awk -v key="$key:" \'$1==key{print $2}\' /proc/self/status); '
        'test $((0x$value & 0x08000000)) -eq 0 || exit 43; done; '
        'echo verified-exec']))==0
    mark(stage+'.exit', status=shell_status)
    stage='python-proc-probe'
    probe, python = sys.argv[2:4]
    mark(stage+'.begin', executable=python)
    assert (probe_status := manager.exec(config.id, [python, '-S', '-c', probe,
        os.readlink('/proc/{}/ns/mnt'.format(running.pid)), *sys.argv[4:]],
        env={'PYTHONHOME': '/usr'})) == 0
    mark(stage+'.exit', status=probe_status)
    stage='stop'
    mark(stage+'.begin', timeout=1, workload=running.pid, supervisor=running.supervisor_pid)
    assert (stop_result := manager.stop(config.id,timeout=1))
    mark(stage+'.exit', result=stop_result)
    assert load_container_config(config.id).status=='stopped'
    print('verified-stop')
except BaseException:
    primary_failure=True
    print('rootexec-original-failure ' + repr(dict(stage=stage,
        shell_status=shell_status, probe_status=probe_status, stop_result=stop_result)),
        file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
    raise
finally:
    try:
        mark('cleanup.begin', primary_failure=primary_failure)
        current=load_container_config(config.id)
        if current and current.status=='running':
            mark('cleanup-stop.begin', timeout=0)
            cleanup_result=manager.stop(config.id,timeout=0)
            mark('cleanup-stop.exit', result=cleanup_result)
        mark('cleanup.exit')
    except BaseException:
        print('rootexec-cleanup-failure primary_failure=' + str(primary_failure),
            file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        if not primary_failure:
            raise
"""
    result = _run_root_container(
        tmp_path,
        [
            sys.executable,
            "-c",
            script,
            rootfs,
            _PROC_PROTECTION_INSPECTION,
            python,
            *_available_proc_mask_targets(),
        ],
    )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "verified-exec" in result.stdout
    assert "proc-protection-inspected" in result.stdout
    assert "verified-stop" in result.stdout


def test_readonly_proc_and_private_inert_control_masks(tmp_path):
    """Inspect the real policy without writes or write-open probes to procfs."""
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)
    python = _install_python_memory_hog(rootfs)

    result = _run_root_container(
        tmp_path,
        [
            sys.executable,
            "-m",
            "mini_docker",
            "run",
            "--no-overlay",
            "--env",
            "PYTHONHOME=/usr",
            rootfs,
            "--",
            python,
            "-S",
            "-c",
            _PROC_PROTECTION_INSPECTION,
            "-",
            *_available_proc_mask_targets(),
        ],
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "proc-protection-inspected" in result.stdout


def test_private_devices_and_nodev_image_volume_and_submount(tmp_path):
    """Real mounts; only harmless /dev/null fixture nodes, never raw disks."""
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)
    source = tmp_path / "devices-volume"
    source.mkdir()
    script = r'''
import os, stat, subprocess, sys
from mini_docker.filesystem import mount, MS_BIND, MS_NOSUID, MS_NOEXEC
rootfs, volume = sys.argv[1:]
# These mutations and mounts occur only under the disposable outer namespace.
def metadata(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid)
real_null = os.open('/dev/null', os.O_PATH | os.O_CLOEXEC)
real_before = metadata(os.fstat(real_null))
fixture_dev = os.path.join(os.path.dirname(volume), 'controller-dev')
os.mkdir(fixture_dev)
# Host pytest directories may be nodev (for example /tmp on hardened hosts).
# Only this tiny private fixture mount permits its harmless null device; never
# relax /tmp or the real host /dev. The outer namespace unmounts it on exit.
mount('tmpfs', fixture_dev, 'tmpfs', MS_NOSUID | MS_NOEXEC, 'size=1m,mode=0700')
fixture_null = os.path.join(fixture_dev, 'null')
os.mknod(fixture_null, stat.S_IFCHR | 0o666, os.makedev(1, 3))
fixture_before = metadata(os.stat(fixture_null))
# Even a regression can touch only this disposable inode, never real host null.
mount(fixture_null, '/dev/null', None, MS_BIND)
for path in (rootfs + '/image-null', volume + '/volume-null'):
    os.mknod(path, stat.S_IFCHR | 0o666, os.makedev(1, 3))
os.mkdir(volume + '/nested')
mount(volume, volume + '/nested', None, MS_BIND)
shell = r"""
set -e
test -c /dev/null
test -c /dev/zero
test -d /dev/pts
test -d /dev/shm
test "$(/bin/busybox stat -c %a /dev/shm)" = '1777'
echo private-shared-memory > /dev/shm/proof
test "$(/bin/busybox cat /dev/shm/proof)" = 'private-shared-memory'
test "$(/bin/busybox readlink /dev/ptmx)" = 'pts/ptmx'
test ! -e /dev/console
test ! -e /dev/disk
test ! -e /.pivot_old
echo harmless > /dev/null
if /bin/busybox chmod 000 /dev/null; then exit 53; fi
if /bin/busybox chown 12345:12345 /dev/null; then exit 54; fi
test -c /proc/self/fd/0
if /bin/busybox chmod 000 /proc/self/fd/0; then exit 55; fi
for path in /image-null /data/volume-null /data/nested/volume-null; do
    test -c "$path"
    if /bin/busybox cat "$path" >/dev/null 2>/dev/null; then exit 51; fi
done
if /bin/busybox mknod /dev/blocked b 7 0; then exit 52; fi
test ! -e /dev/blocked
for key in CapEff CapPrm CapBnd; do
    value=$(/bin/busybox awk -v key="$key:" '$1==key{print $2}' /proc/self/status)
    test $((0x$value & 0x08000000)) -eq 0
done
echo devices-confined
"""
result = subprocess.run([sys.executable, '-m', 'mini_docker', 'run', '--no-overlay',
    '--volume', volume + ':/data:rw', rootfs, '--', '/bin/sh', '-c', shell],
    input='', text=True, capture_output=True, timeout=20)
assert metadata(os.stat(fixture_null)) == fixture_before, 'fixture device metadata changed'
assert metadata(os.fstat(real_null)) == real_before, 'real host device metadata changed'
os.close(real_null)
print(result.stdout, end='')
print(result.stderr, end='', file=sys.stderr)
assert result.returncode == 0, result.returncode
print('host-device-metadata-unchanged')
'''
    result = _run_root_container(
        tmp_path, [sys.executable, "-c", script, rootfs, str(source)]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "devices-confined" in result.stdout
    assert "host-device-metadata-unchanged" in result.stdout


@pytest.mark.parametrize(
    "unsafe", ["dev", "proc", "sys", ".pivot_old", "volume-dotdot", "volume-symlink"]
)
def test_unsafe_staging_fails_without_touching_outside(tmp_path, unsafe):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)
    outside, source = tmp_path / "outside", tmp_path / "source"
    outside.mkdir()
    source.mkdir()
    args = [sys.executable, "-m", "mini_docker", "run", "--no-overlay"]
    if unsafe.startswith("volume-"):
        target = "/../outside/created"
        if unsafe == "volume-symlink":
            os.symlink(outside, os.path.join(rootfs, "app"), target_is_directory=True)
            target = "/app/created"
        args += ["--volume", f"{source}:{target}:rw"]
    else:
        staging = os.path.join(rootfs, unsafe)
        if os.path.exists(staging):
            os.rmdir(staging)
        os.symlink(outside, staging, target_is_directory=True)
    args += [rootfs, "--", "/bin/sh", "-c", "echo unsafe-workload-executed"]
    result = _run_root_container(tmp_path, args)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "unsafe-workload-executed" not in result.stdout
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("failure", ["private", "detach"])
def test_required_mount_failure_never_falls_back_or_execs(tmp_path, failure):
    _require_root()
    _require_cgroups_v2()
    rootfs = _copy_rootfs(tmp_path)
    script = r"""
import sys, time
from mini_docker import container as runtime, filesystem as fs
from mini_docker.metadata import load_container_config
def deny(*args, **kwargs):
    raise fs.FilesystemError('injected required mount failure')
def forbidden_fallback(*args):
    print('BAD-CHROOT-FALLBACK', flush=True)
    raise AssertionError('no chroot fallback allowed')
if sys.argv[2] == 'private':
    runtime.mount = deny
else:
    fs.umount = deny
fs.chroot = forbidden_fallback
manager = runtime.Container()
config = manager.create(sys.argv[1], ['/bin/sh', '-c', 'echo UNSAFE-WORKLOAD-EXECUTED'],
                        use_overlay=False, detach=True)
manager.start(config.id, attach=True)
deadline = time.monotonic() + 8
while time.monotonic() < deadline:
    state = load_container_config(config.id)
    if state.status == 'stopped':
        break
    time.sleep(.02)
assert state.status == 'stopped', state
assert not state.exec_ready
assert state.exit_code != 0, state
print('aborted-before-exec')
"""
    result = _run_root_container(
        tmp_path, [sys.executable, "-c", script, rootfs, failure]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "aborted-before-exec" in result.stdout
    assert "BAD-CHROOT-FALLBACK" not in result.stdout
    assert "UNSAFE-WORKLOAD-EXECUTED" not in result.stdout
