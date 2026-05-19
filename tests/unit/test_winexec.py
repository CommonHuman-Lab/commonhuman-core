# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Unit tests for commonhuman_core.winexec — impacket mocked throughout."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch, call

import pytest

import commonhuman_core.winexec as winexec_mod
from commonhuman_core.winexec import (
    WinExecClient,
    _require_impacket,
    _split_share_path,
    _EMPTY_LM,
)


# ---------------------------------------------------------------------------
# _split_share_path
# ---------------------------------------------------------------------------

class TestSplitSharePath:
    def test_basic_c_share(self):
        share, path = _split_share_path(r"C$\Windows\Temp\file.txt")
        assert share == "C$"
        assert path == r"\Windows\Temp\file.txt"

    def test_admin_share(self):
        share, path = _split_share_path(r"ADMIN$\file.txt")
        assert share == "ADMIN$"
        assert path == r"\file.txt"

    def test_forward_slashes_normalised(self):
        share, path = _split_share_path("C$/Windows/Temp/file.txt")
        assert share == "C$"
        assert path == r"\Windows\Temp\file.txt"

    def test_share_only(self):
        share, path = _split_share_path("C$")
        assert share == "C$"
        assert path == "\\"

    def test_leading_backslash_stripped(self):
        share, path = _split_share_path(r"\\C$\path\to\file")
        assert share == "C$"
        assert path == r"\path\to\file"


# ---------------------------------------------------------------------------
# _require_impacket guard
# ---------------------------------------------------------------------------

class TestRequireImpacket:
    def test_raises_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(winexec_mod, "_IMPACKET_AVAILABLE", False)
        with pytest.raises(ImportError, match="impacket is required"):
            _require_impacket()

    def test_no_error_when_available(self, monkeypatch):
        monkeypatch.setattr(winexec_mod, "_IMPACKET_AVAILABLE", True)
        _require_impacket()


# ---------------------------------------------------------------------------
# _EMPTY_LM constant
# ---------------------------------------------------------------------------

class TestEmptyLm:
    def test_correct_value(self):
        assert _EMPTY_LM == "aad3b435b51404eeaad3b435b51404ee"

    def test_correct_length(self):
        assert len(_EMPTY_LM) == 32


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_impacket():
    """Patch impacket and return mock SMBConnection class + instance.

    Uses create=True because SMBConnection is not in the module namespace
    when impacket is not installed (conditional import under try/except).
    """
    mock_smb_instance = MagicMock()
    mock_smb_class = MagicMock(return_value=mock_smb_instance)

    with patch.object(winexec_mod, "_IMPACKET_AVAILABLE", True):
        with patch("commonhuman_core.winexec.SMBConnection", mock_smb_class, create=True):
            yield mock_smb_class, mock_smb_instance


# ---------------------------------------------------------------------------
# WinExecClient construction
# ---------------------------------------------------------------------------

class TestWinExecClientInit:
    def test_raises_without_impacket(self, monkeypatch):
        monkeypatch.setattr(winexec_mod, "_IMPACKET_AVAILABLE", False)
        with pytest.raises(ImportError):
            WinExecClient()


# ---------------------------------------------------------------------------
# WinExecClient.connect
# ---------------------------------------------------------------------------

class TestWinExecClientConnect:
    def test_connect_with_nt_hash(self, mock_impacket):
        cls, inst = mock_impacket
        nt = "a" * 32
        client = WinExecClient.connect("10.0.0.1", user="Administrator", nt_hash=nt, domain="CORP")
        inst.login.assert_called_once_with(
            "Administrator", "", "CORP", lmhash=_EMPTY_LM, nthash=nt
        )
        assert client._nt_hash == nt
        assert client._user == "Administrator"
        assert client._host == "10.0.0.1"

    def test_connect_with_password(self, mock_impacket):
        cls, inst = mock_impacket
        client = WinExecClient.connect("10.0.0.1", user="user", password="Secret1!")
        inst.login.assert_called_once_with("user", "Secret1!", "")

    def test_connect_stores_host_and_domain(self, mock_impacket):
        cls, inst = mock_impacket
        client = WinExecClient.connect("192.168.1.5", user="u", password="p", domain="LAB")
        assert client._host == "192.168.1.5"
        assert client._domain == "LAB"

    def test_raises_without_impacket(self, monkeypatch):
        monkeypatch.setattr(winexec_mod, "_IMPACKET_AVAILABLE", False)
        with pytest.raises(ImportError):
            WinExecClient.connect("host", user="user", password="pw")


# ---------------------------------------------------------------------------
# WinExecClient.get_file / put_file
# ---------------------------------------------------------------------------

class TestWinExecClientFileTransfer:
    def _make_client(self, mock_impacket):
        cls, smb = mock_impacket
        c = WinExecClient()
        c._smb = smb
        return c, smb

    def test_get_file_reads_via_smb(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)

        def fake_get(share, path, write_cb):
            write_cb(b"file-data")

        smb.getFile.side_effect = fake_get
        result = c.get_file(r"C$\Windows\System32\file.txt")
        assert result == b"file-data"
        smb.getFile.assert_called_once_with("C$", r"\Windows\System32\file.txt", pytest.any if False else smb.getFile.call_args[0][2])

    def test_get_file_raises_when_not_connected(self, mock_impacket):
        cls, smb = mock_impacket
        c = WinExecClient()
        c._smb = None
        with pytest.raises(RuntimeError, match="Not connected"):
            c.get_file(r"C$\file.txt")

    def test_put_file_uploads_via_smb(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        c.put_file(b"content", r"C$\Temp\output.txt")
        smb.putFile.assert_called_once()
        args = smb.putFile.call_args[0]
        assert args[0] == "C$"
        assert args[1] == r"\Temp\output.txt"

    def test_put_file_raises_when_not_connected(self, mock_impacket):
        cls, smb = mock_impacket
        c = WinExecClient()
        c._smb = None
        with pytest.raises(RuntimeError, match="Not connected"):
            c.put_file(b"x", r"C$\file.txt")


# ---------------------------------------------------------------------------
# WinExecClient.execute / _wmi_create
# ---------------------------------------------------------------------------

class TestWinExecClientExecute:
    def _make_client(self, mock_impacket):
        cls, smb = mock_impacket
        c = WinExecClient()
        c._smb = smb
        c._host = "10.0.0.1"
        c._user = "Administrator"
        c._nt_hash = "a" * 32
        c._password = ""
        c._domain = "CORP"
        c._timeout = 30
        return c, smb

    def test_execute_returns_stdout_on_success(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)

        def fake_get(share, path, write_cb):
            write_cb(b"NT AUTHORITY\\SYSTEM\r\n")

        smb.getFile.side_effect = fake_get

        with patch.object(c, "_wmi_create", return_value=0):
            stdout, stderr, code = c.execute("whoami", timeout=5)

        assert "SYSTEM" in stdout
        assert code == 0

    def test_execute_returns_error_when_wmi_fails(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        with patch.object(c, "_wmi_create", return_value=2):
            stdout, stderr, code = c.execute("whoami", timeout=5)

        assert stdout == ""
        assert "ReturnValue" in stderr or "2" in stderr
        assert code == 2

    def test_execute_returns_timeout_when_file_never_appears(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        smb.getFile.side_effect = OSError("not found")

        with patch.object(c, "_wmi_create", return_value=0):
            with patch("commonhuman_core.winexec.time.sleep"):
                with patch("commonhuman_core.winexec.time.monotonic", side_effect=[0, 0, 999]):
                    stdout, stderr, code = c.execute("whoami", timeout=1)

        assert code == -1
        assert "timeout" in stderr

    def test_wmi_create_raises_on_dcom_error(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        mock_dcom_cls = MagicMock(side_effect=OSError("refused"))

        with patch("commonhuman_core.winexec.DCOMConnection", mock_dcom_cls, create=True):
            with patch("commonhuman_core.winexec._wmi", MagicMock(), create=True):
                with patch("commonhuman_core.winexec._NULL", MagicMock(), create=True):
                    with pytest.raises(OSError):
                        c._wmi_create("whoami")

    def test_wmi_create_disconnects_dcom_even_on_error(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        mock_dcom_inst = MagicMock()
        mock_dcom_cls = MagicMock(return_value=mock_dcom_inst)
        mock_dcom_inst.CoCreateInstanceEx.side_effect = RuntimeError("fail")

        with patch("commonhuman_core.winexec.DCOMConnection", mock_dcom_cls, create=True):
            with patch("commonhuman_core.winexec._wmi", MagicMock(), create=True):
                with patch("commonhuman_core.winexec._NULL", MagicMock(), create=True):
                    with pytest.raises(RuntimeError):
                        c._wmi_create("whoami")

        mock_dcom_inst.disconnect.assert_called_once()

    def test_wmi_create_success_path(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        mock_dcom_inst = MagicMock()
        mock_dcom_cls = MagicMock(return_value=mock_dcom_inst)

        mock_wmi = MagicMock()
        mock_svc = MagicMock()
        mock_login = MagicMock()
        mock_login.NTLMLogin.return_value = mock_svc
        mock_wmi.IWbemLevel1Login.return_value = mock_login

        mock_proc_cls = MagicMock()
        mock_svc.GetObject.return_value = (mock_proc_cls, MagicMock())

        mock_out = MagicMock()
        mock_out.ReturnValue = 0
        mock_svc.ExecMethod.return_value = mock_out

        mock_dcom_inst.CoCreateInstanceEx.return_value = MagicMock()

        with patch("commonhuman_core.winexec.DCOMConnection", mock_dcom_cls, create=True):
            with patch("commonhuman_core.winexec._wmi", mock_wmi, create=True):
                with patch("commonhuman_core.winexec._NULL", MagicMock(), create=True):
                    ret = c._wmi_create("whoami")

        assert ret == 0

    def test_wmi_create_disconnect_raises_suppressed(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        mock_dcom_inst = MagicMock()
        mock_dcom_cls = MagicMock(return_value=mock_dcom_inst)
        mock_dcom_inst.CoCreateInstanceEx.side_effect = RuntimeError("fail")
        mock_dcom_inst.disconnect.side_effect = OSError("already closed")

        with patch("commonhuman_core.winexec.DCOMConnection", mock_dcom_cls, create=True):
            with patch("commonhuman_core.winexec._wmi", MagicMock(), create=True):
                with patch("commonhuman_core.winexec._NULL", MagicMock(), create=True):
                    with pytest.raises(RuntimeError):
                        c._wmi_create("whoami")

    def test_execute_success_with_deletefile_failing(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        smb.deleteFile.side_effect = OSError("access denied")

        def fake_get(share, path, write_cb):
            write_cb(b"result\n")

        smb.getFile.side_effect = fake_get

        with patch.object(c, "_wmi_create", return_value=0):
            stdout, stderr, code = c.execute("whoami", timeout=5)

        assert stdout == "result\n"
        assert code == 0


# ---------------------------------------------------------------------------
# WinExecClient.close / context manager
# ---------------------------------------------------------------------------

class TestWinExecClientClose:
    def _make_client(self, mock_impacket):
        cls, smb = mock_impacket
        c = WinExecClient()
        c._smb = smb
        return c, smb

    def test_close_calls_logoff(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        c.close()
        smb.logoff.assert_called_once()
        assert c._smb is None

    def test_close_handles_logoff_exception(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        smb.logoff.side_effect = Exception("already disconnected")
        c.close()  # must not raise
        assert c._smb is None

    def test_close_when_not_connected_is_noop(self, mock_impacket):
        cls, smb = mock_impacket
        c = WinExecClient()
        c._smb = None
        c.close()  # must not raise

    def test_enter_returns_self(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        assert c.__enter__() is c

    def test_exit_calls_close(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        c.__exit__(None, None, None)
        smb.logoff.assert_called_once()

    def test_used_as_context_manager(self, mock_impacket):
        c, smb = self._make_client(mock_impacket)
        with c:
            pass
        smb.logoff.assert_called_once()
