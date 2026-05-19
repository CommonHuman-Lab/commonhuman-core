# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""
WinExecClient — thin impacket wrapper for Windows remote command execution and file transfer.

Supports Pass-the-Hash authentication (NT hash, no plaintext password required).
Optional dependency: pip install commonhuman-core[smb]
"""

from __future__ import annotations

import io
import time
import uuid
from typing import Optional, Tuple

try:
    from impacket.smbconnection import SMBConnection  # pragma: no cover
    from impacket.dcerpc.v5.dcomrt import DCOMConnection  # pragma: no cover
    from impacket.dcerpc.v5.dcom import wmi as _wmi  # pragma: no cover
    from impacket.dcerpc.v5.dtypes import NULL as _NULL  # pragma: no cover
    _IMPACKET_AVAILABLE = True  # pragma: no cover
except ImportError:
    _IMPACKET_AVAILABLE = False

# Canonical empty LM hash — required by impacket for NT-hash-only auth
_EMPTY_LM = "aad3b435b51404eeaad3b435b51404ee"


def _require_impacket() -> None:
    if not _IMPACKET_AVAILABLE:
        raise ImportError(
            "impacket is required for Windows remote execution. "
            "Install it with: pip install commonhuman-core[smb]"
        )


class WinExecClient:
    """Thin wrapper around impacket for Windows remote command execution via SMB/WMI.

    Supports Pass-the-Hash (NT hash) and plaintext password authentication.

    Usage::

        with WinExecClient.connect(
            "192.168.1.10", user="Administrator",
            nt_hash="aad3b435...", domain="CORP",
        ) as client:
            stdout, stderr, code = client.execute("whoami /all")
            raw = client.get_file(r"C$\\Windows\\System32\\drivers\\etc\\hosts")
    """

    def __init__(self) -> None:
        _require_impacket()
        self._host: str = ""
        self._user: str = ""
        self._nt_hash: str = ""
        self._password: str = ""
        self._domain: str = ""
        self._timeout: int = 30
        self._smb: Optional["SMBConnection"] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    @classmethod
    def connect(
        cls,
        host: str,
        user: str,
        nt_hash: str = "",
        password: str = "",
        domain: str = "",
        port: int = 445,
        timeout: int = 30,
    ) -> "WinExecClient":
        """Open an authenticated SMB connection and return a connected WinExecClient.

        Pass-the-Hash: supply *nt_hash* (32-char hex NT hash), leave *password* empty.
        Password auth:  supply *password*, leave *nt_hash* empty.
        """
        _require_impacket()
        obj = cls()
        obj._host = host
        obj._user = user
        obj._nt_hash = nt_hash
        obj._password = password
        obj._domain = domain
        obj._timeout = timeout

        smb: "SMBConnection" = SMBConnection(host, host, sess_port=port, timeout=timeout)
        if nt_hash:
            smb.login(user, "", domain, lmhash=_EMPTY_LM, nthash=nt_hash)
        else:
            smb.login(user, password, domain)
        obj._smb = smb
        return obj

    # ------------------------------------------------------------------
    # Remote execution (WMI + stdout redirect via temp file)
    # ------------------------------------------------------------------

    def execute(self, command: str, timeout: int = 60) -> Tuple[str, str, int]:
        """Execute *command* on the remote host via WMI Win32_Process.

        Stdout and stderr are captured by redirecting into a temp file on the
        remote host's ``%SystemRoot%\\Temp``, then downloading via SMB.

        Returns ``(stdout, stderr, exit_code)``.
        """
        tag = uuid.uuid4().hex[:8]
        remote_out = f"\\Windows\\Temp\\_vr{tag}.txt"
        wrapped = f"cmd.exe /Q /c {command} > {remote_out} 2>&1"

        ret = self._wmi_create(wrapped)
        if ret != 0:
            return "", f"[WMI Win32_Process.Create returned {ret}]", ret

        # Poll for the output file — the process writes async
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(0.5)
            try:
                data = self.get_file(f"C${remote_out}")
                stdout = data.decode("utf-8", errors="replace")
                try:
                    self._smb.deleteFile("C$", remote_out)  # type: ignore[union-attr]
                except Exception:
                    pass
                return stdout, "", 0
            except Exception:
                continue

        return "", "[timeout waiting for process output]", -1

    def _wmi_create(self, command: str) -> int:
        """Create a process via WMI DCOM. Returns Win32_Process.Create ReturnValue."""
        dcom: "DCOMConnection" = DCOMConnection(
            self._host,
            self._user,
            self._password,
            self._domain,
            lmhash=_EMPTY_LM if self._nt_hash else "",
            nthash=self._nt_hash,
            oxidResolver=True,
        )
        try:
            iface = dcom.CoCreateInstanceEx(_wmi.CLSID_WbemLevel1Login, _wmi.IID_IWbemLevel1Login)
            login = _wmi.IWbemLevel1Login(iface)
            svc = login.NTLMLogin("//./root/cimv2", _NULL, _NULL)
            login.RemRelease()

            proc_cls, _ = svc.GetObject("Win32_Process")
            proc_in = proc_cls.SpawnInstance()
            proc_in.CommandLine = command
            proc_in.CurrentDirectory = "C:\\"

            out = svc.ExecMethod("Win32_Process", "Create", proc_in)
            return int(getattr(out, "ReturnValue", -1))
        except Exception:
            raise
        finally:
            try:
                dcom.disconnect()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # File transfer (SMB shares)
    # ------------------------------------------------------------------

    def get_file(self, remote_path: str) -> bytes:
        """Download a file via SMB.

        *remote_path* uses admin-share format: ``C$\\path\\to\\file``
        (the first component is the share name).
        """
        if not self._smb:
            raise RuntimeError("Not connected")
        share, path = _split_share_path(remote_path)
        buf = io.BytesIO()
        self._smb.getFile(share, path, buf.write)
        return buf.getvalue()

    def put_file(self, data: bytes, remote_path: str) -> None:
        """Upload *data* to *remote_path* via SMB."""
        if not self._smb:
            raise RuntimeError("Not connected")
        share, path = _split_share_path(remote_path)
        self._smb.putFile(share, path, io.BytesIO(data).read)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        if self._smb:
            try:
                self._smb.logoff()
            except Exception:
                pass
            self._smb = None

    def __enter__(self) -> "WinExecClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _split_share_path(remote_path: str) -> Tuple[str, str]:
    """Split ``C$\\Windows\\Temp\\file.txt`` into ``("C$", "\\Windows\\Temp\\file.txt")``."""
    normalised = remote_path.replace("/", "\\").lstrip("\\")
    parts = normalised.split("\\", 1)
    share = parts[0]
    path = "\\" + parts[1] if len(parts) > 1 else "\\"
    return share, path
