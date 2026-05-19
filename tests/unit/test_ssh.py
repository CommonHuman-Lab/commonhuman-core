# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Unit tests for commonhuman_core.ssh — paramiko mocked throughout."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch, call

import pytest

import commonhuman_core.ssh as ssh_mod
from commonhuman_core.ssh import SshClient, _require_paramiko


# ---------------------------------------------------------------------------
# _require_paramiko guard
# ---------------------------------------------------------------------------

class TestRequireParamiko:
    def test_raises_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(ssh_mod, "_PARAMIKO_AVAILABLE", False)
        with pytest.raises(ImportError, match="paramiko is required"):
            _require_paramiko()

    def test_no_error_when_available(self, monkeypatch):
        monkeypatch.setattr(ssh_mod, "_PARAMIKO_AVAILABLE", True)
        _require_paramiko()  # must not raise


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_paramiko():
    """Patch paramiko at the module level and return the mock namespace."""
    with patch.object(ssh_mod, "_PARAMIKO_AVAILABLE", True):
        with patch("commonhuman_core.ssh.paramiko") as mp:
            mock_client = MagicMock()
            mp.SSHClient.return_value = mock_client
            mp.AutoAddPolicy.return_value = MagicMock()
            yield mp, mock_client


# ---------------------------------------------------------------------------
# SshClient construction
# ---------------------------------------------------------------------------

class TestSshClientInit:
    def test_raises_without_paramiko(self, monkeypatch):
        monkeypatch.setattr(ssh_mod, "_PARAMIKO_AVAILABLE", False)
        with pytest.raises(ImportError):
            SshClient()

    def test_sets_missing_host_key_policy(self, mock_paramiko):
        mp, mock_client = mock_paramiko
        SshClient()
        mock_client.set_missing_host_key_policy.assert_called_once()


# ---------------------------------------------------------------------------
# SshClient.connect
# ---------------------------------------------------------------------------

class TestSshClientConnect:
    def test_connects_with_password(self, mock_paramiko):
        mp, mock_client = mock_paramiko
        client = SshClient.connect("192.168.1.1", user="root", password="secret")
        mock_client.connect.assert_called_once()
        kwargs = mock_client.connect.call_args.kwargs
        assert kwargs["hostname"] == "192.168.1.1"
        assert kwargs["username"] == "root"
        assert kwargs["password"] == "secret"

    def test_connects_with_key_path(self, mock_paramiko, tmp_path):
        mp, mock_client = mock_paramiko
        key_file = str(tmp_path / "id_rsa")
        client = SshClient.connect("10.0.0.1", user="admin", key_path=key_file)
        kwargs = mock_client.connect.call_args.kwargs
        assert kwargs["key_filename"] == key_file

    def test_default_port_22(self, mock_paramiko):
        mp, mock_client = mock_paramiko
        SshClient.connect("host", user="user", password="pw")
        kwargs = mock_client.connect.call_args.kwargs
        assert kwargs["port"] == 22

    def test_custom_port(self, mock_paramiko):
        mp, mock_client = mock_paramiko
        SshClient.connect("host", user="user", password="pw", port=2222)
        kwargs = mock_client.connect.call_args.kwargs
        assert kwargs["port"] == 2222

    def test_look_for_keys_false_when_password_given(self, mock_paramiko):
        mp, mock_client = mock_paramiko
        SshClient.connect("host", user="user", password="pw")
        kwargs = mock_client.connect.call_args.kwargs
        assert kwargs["look_for_keys"] is False
        assert kwargs["allow_agent"] is False

    def test_look_for_keys_true_when_no_creds(self, mock_paramiko):
        mp, mock_client = mock_paramiko
        SshClient.connect("host", user="user")
        kwargs = mock_client.connect.call_args.kwargs
        assert kwargs["look_for_keys"] is True
        assert kwargs["allow_agent"] is True

    def test_raises_without_paramiko(self, monkeypatch):
        monkeypatch.setattr(ssh_mod, "_PARAMIKO_AVAILABLE", False)
        with pytest.raises(ImportError):
            SshClient.connect("host", user="user", password="pw")


# ---------------------------------------------------------------------------
# SshClient.run / run_many
# ---------------------------------------------------------------------------

class TestSshClientRun:
    def _make_client(self, mock_paramiko):
        mp, mock_underlying = mock_paramiko
        c = SshClient()
        c._client = mock_underlying
        return c, mock_underlying

    def test_run_returns_stdout_stderr_code(self, mock_paramiko):
        c, mc = self._make_client(mock_paramiko)
        stdout_f = MagicMock()
        stdout_f.read.return_value = b"output\n"
        stdout_f.channel.recv_exit_status.return_value = 0
        stderr_f = MagicMock()
        stderr_f.read.return_value = b""
        mc.exec_command.return_value = (MagicMock(), stdout_f, stderr_f)

        out, err, code = c.run("whoami")
        assert out == "output\n"
        assert err == ""
        assert code == 0

    def test_run_many_calls_run_for_each(self, mock_paramiko):
        c, mc = self._make_client(mock_paramiko)
        stdout_f = MagicMock()
        stdout_f.read.return_value = b"x"
        stdout_f.channel.recv_exit_status.return_value = 0
        stderr_f = MagicMock()
        stderr_f.read.return_value = b""
        mc.exec_command.return_value = (MagicMock(), stdout_f, stderr_f)

        results = c.run_many(["cmd1", "cmd2", "cmd3"])
        assert len(results) == 3
        assert mc.exec_command.call_count == 3


# ---------------------------------------------------------------------------
# SshClient.get_file / put_file / list_dir
# ---------------------------------------------------------------------------

class TestSshClientFileTransfer:
    def _make_client(self, mock_paramiko):
        mp, mock_underlying = mock_paramiko
        c = SshClient()
        c._client = mock_underlying
        return c, mock_underlying

    def test_get_file_returns_bytes(self, mock_paramiko):
        c, mc = self._make_client(mock_paramiko)
        mock_sftp = MagicMock()
        mc.open_sftp.return_value = mock_sftp

        def fake_getfo(path, buf):
            buf.write(b"file-content")

        mock_sftp.getfo.side_effect = fake_getfo
        result = c.get_file("/etc/shadow")
        assert result == b"file-content"
        mock_sftp.close.assert_called_once()

    def test_put_file_uploads(self, mock_paramiko):
        c, mc = self._make_client(mock_paramiko)
        mock_sftp = MagicMock()
        mc.open_sftp.return_value = mock_sftp
        c.put_file(b"data", "/tmp/output.txt")
        mock_sftp.putfo.assert_called_once()
        mock_sftp.close.assert_called_once()

    def test_list_dir_returns_filenames(self, mock_paramiko):
        c, mc = self._make_client(mock_paramiko)
        mock_sftp = MagicMock()
        mc.open_sftp.return_value = mock_sftp
        mock_sftp.listdir.return_value = ["id_rsa", "id_rsa.pub"]
        result = c.list_dir("/home/user/.ssh")
        assert result == ["id_rsa", "id_rsa.pub"]

    def test_list_dir_returns_empty_on_oserror(self, mock_paramiko):
        c, mc = self._make_client(mock_paramiko)
        mock_sftp = MagicMock()
        mc.open_sftp.return_value = mock_sftp
        mock_sftp.listdir.side_effect = OSError("not found")
        assert c.list_dir("/nonexistent") == []


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

class TestSshClientContextManager:
    def test_enter_returns_self(self, mock_paramiko):
        mp, mc = mock_paramiko
        c = SshClient()
        c._client = mc
        assert c.__enter__() is c

    def test_exit_calls_close(self, mock_paramiko):
        mp, mc = mock_paramiko
        c = SshClient()
        c._client = mc
        c.__exit__(None, None, None)
        mc.close.assert_called_once()

    def test_used_as_context_manager(self, mock_paramiko):
        mp, mc = mock_paramiko
        c = SshClient()
        c._client = mc
        with c:
            pass
        mc.close.assert_called_once()
