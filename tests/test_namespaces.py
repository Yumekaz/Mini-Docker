import os
import ctypes
import pytest
from unittest import mock

from mini_docker import namespaces
from mini_docker.namespaces import NamespaceError


@pytest.fixture
def mock_libc(monkeypatch):
    mock_libc = mock.Mock()
    # By default, assume success
    mock_libc.unshare.return_value = 0
    mock_libc.setns.return_value = 0
    mock_libc.sethostname.return_value = 0
    monkeypatch.setattr(namespaces, "libc", mock_libc)
    return mock_libc


@pytest.fixture
def mock_ctypes(monkeypatch):
    mock_get_errno = mock.Mock(return_value=1)
    monkeypatch.setattr(namespaces.ctypes, "get_errno", mock_get_errno)
    return mock_get_errno


class TestSyscallWrappers:
    def test_unshare_success(self, mock_libc):
        assert namespaces.unshare(namespaces.CLONE_NEWPID) == 0
        mock_libc.unshare.assert_called_once_with(namespaces.CLONE_NEWPID)

    def test_unshare_failure(self, mock_libc, mock_ctypes):
        mock_libc.unshare.return_value = -1
        with pytest.raises(NamespaceError, match="unshare failed with errno 1"):
            namespaces.unshare(namespaces.CLONE_NEWPID)

    def test_setns_success(self, mock_libc):
        assert namespaces.setns(42, namespaces.CLONE_NEWPID) == 0
        mock_libc.setns.assert_called_once_with(42, namespaces.CLONE_NEWPID)

    def test_setns_failure(self, mock_libc, mock_ctypes):
        mock_libc.setns.return_value = -1
        with pytest.raises(NamespaceError, match="setns failed with errno 1"):
            namespaces.setns(42, namespaces.CLONE_NEWPID)

    def test_sethostname_success(self, mock_libc):
        assert namespaces.sethostname("test-host") == 0
        mock_libc.sethostname.assert_called_once_with(b"test-host", 9)

    def test_sethostname_failure(self, mock_libc, mock_ctypes):
        mock_libc.sethostname.return_value = -1
        with pytest.raises(NamespaceError, match="sethostname failed with errno 1"):
            namespaces.sethostname("test-host")


class TestNamespaceFunctions:
    def test_create_namespaces(self, mock_libc):
        flags = namespaces.create_namespaces(["pid", "uts"], hostname="myhost")
        expected_flags = namespaces.CLONE_NEWPID | namespaces.CLONE_NEWUTS
        assert flags == expected_flags
        mock_libc.unshare.assert_called_once_with(expected_flags)
        mock_libc.sethostname.assert_called_once_with(b"myhost", 6)

    def test_create_namespaces_rootless(self, mock_libc):
        flags = namespaces.create_namespaces(["pid"], rootless=True)
        expected_flags = namespaces.CLONE_NEWPID | namespaces.CLONE_NEWUSER
        assert flags == expected_flags
        mock_libc.unshare.assert_called_once_with(expected_flags)

    @mock.patch("os.path.exists")
    @mock.patch("os.open")
    @mock.patch("os.close")
    def test_enter_namespace(self, mock_close, mock_open, mock_exists, mock_libc):
        mock_exists.return_value = True
        mock_open.return_value = 42

        namespaces.enter_namespace(1234, "pid")

        mock_exists.assert_called_once_with("/proc/1234/ns/pid")
        mock_open.assert_called_once_with("/proc/1234/ns/pid", os.O_RDONLY)
        mock_libc.setns.assert_called_once_with(42, namespaces.CLONE_NEWPID)
        mock_close.assert_called_once_with(42)

    def test_enter_namespace_unknown_type(self):
        with pytest.raises(NamespaceError, match="Unknown namespace type"):
            namespaces.enter_namespace(1234, "unknown")

    @mock.patch("os.path.exists")
    def test_enter_namespace_not_exists(self, mock_exists):
        mock_exists.return_value = False
        with pytest.raises(NamespaceError, match="Namespace path does not exist"):
            namespaces.enter_namespace(1234, "pid")

    @mock.patch("mini_docker.namespaces.enter_namespace")
    def test_enter_all_namespaces(self, mock_enter):
        namespaces.enter_all_namespaces(1234, ["pid", "net"])
        mock_enter.assert_has_calls([mock.call(1234, "pid"), mock.call(1234, "net")])

    @mock.patch("mini_docker.namespaces.enter_namespace")
    def test_enter_all_namespaces_ignores_errors(self, mock_enter):
        mock_enter.side_effect = NamespaceError("Not found")
        # Should not raise
        namespaces.enter_all_namespaces(1234, ["pid", "net"])
        assert mock_enter.call_count == 2

    @mock.patch("os.readlink")
    def test_get_namespace_id(self, mock_readlink):
        mock_readlink.return_value = "pid:[12345]"
        assert namespaces.get_namespace_id(1234, "pid") == "pid:[12345]"
        mock_readlink.assert_called_once_with("/proc/1234/ns/pid")

    @mock.patch("os.readlink")
    def test_get_namespace_id_error(self, mock_readlink):
        mock_readlink.side_effect = OSError()
        assert namespaces.get_namespace_id(1234, "pid") is None

    @mock.patch("builtins.open", new_callable=mock.mock_open)
    @mock.patch("os.getuid", return_value=1000)
    @mock.patch("os.getgid", return_value=1000)
    def test_setup_user_namespace(self, mock_getgid, mock_getuid, mock_file):
        namespaces.setup_user_namespace(1234)

        # Check file operations
        mock_file.assert_any_call("/proc/1234/setgroups", "w")
        mock_file.assert_any_call("/proc/1234/uid_map", "w")
        mock_file.assert_any_call("/proc/1234/gid_map", "w")

        # Check writes
        handle = mock_file()
        handle.write.assert_any_call("deny")
        handle.write.assert_any_call("0 1000 1\n")


class TestNamespaceContextManager:
    @mock.patch("os.path.exists")
    @mock.patch("os.open")
    @mock.patch("os.close")
    def test_namespace_context_manager(
        self, mock_close, mock_open, mock_exists, mock_libc
    ):
        mock_exists.return_value = True
        mock_open.return_value = 42

        with namespaces.Namespace(["pid", "uts"], hostname="testhost") as ns:
            assert ns.namespaces == ["pid", "uts"]
            assert ns.hostname == "testhost"

            # create_namespaces should have been called
            mock_libc.unshare.assert_called_once_with(
                namespaces.CLONE_NEWPID | namespaces.CLONE_NEWUTS
            )
            mock_libc.sethostname.assert_called_once_with(b"testhost", 8)

            # original fds should be saved
            assert "pid" in ns.original_ns_fds
            assert "uts" in ns.original_ns_fds

        # exiting context should call setns and close
        assert mock_libc.setns.call_count == 2
        mock_libc.setns.assert_any_call(42, namespaces.CLONE_NEWPID)
        mock_libc.setns.assert_any_call(42, namespaces.CLONE_NEWUTS)

        assert mock_close.call_count == 2
        mock_close.assert_any_call(42)
