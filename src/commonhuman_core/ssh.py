# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""
SshClient — thin paramiko wrapper for remote command execution and file transfer.

Optional dependency: pip install commonhuman-core[ssh]
"""

from __future__ import annotations

from typing import Optional, Tuple

try:
    import paramiko
    _PARAMIKO_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PARAMIKO_AVAILABLE = False


def _require_paramiko() -> None:
    if not _PARAMIKO_AVAILABLE:
        raise ImportError(
            "paramiko is required for SSH support. "
            "Install it with: pip install commonhuman-core[ssh]"
        )


class SshClient:
    """Thin wrapper around paramiko.SSHClient for remote harvesting.

    Usage::

        with SshClient.connect("192.168.1.10", user="root", key_path="~/.ssh/id_rsa") as client:
            stdout, stderr, code = client.run("cat /etc/shadow")
            raw = client.get_file("/etc/krb5.keytab")
    """

    def __init__(self) -> None:
        _require_paramiko()
        self._client: "paramiko.SSHClient" = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    @classmethod
    def connect(
        cls,
        host: str,
        user: str,
        key_path: Optional[str] = None,
        password: Optional[str] = None,
        port: int = 22,
        timeout: int = 30,
    ) -> "SshClient":
        """Open an SSH connection and return a connected SshClient."""
        _require_paramiko()
        obj = cls()
        import os
        connect_kwargs: dict = {
            "hostname": host,
            "username": user,
            "port": port,
            "timeout": timeout,
            "look_for_keys": key_path is None and password is None,
            "allow_agent": key_path is None and password is None,
        }
        if key_path:
            connect_kwargs["key_filename"] = os.path.expanduser(key_path)
        if password:
            connect_kwargs["password"] = password

        obj._client.connect(**connect_kwargs)
        return obj

    # ------------------------------------------------------------------
    # Remote execution
    # ------------------------------------------------------------------

    def run(self, command: str, timeout: int = 60) -> Tuple[str, str, int]:
        """Execute *command* on the remote host.

        Returns ``(stdout, stderr, exit_code)``.
        """
        _, stdout_f, stderr_f = self._client.exec_command(command, timeout=timeout)
        exit_code = stdout_f.channel.recv_exit_status()
        stdout = stdout_f.read().decode("utf-8", errors="replace")
        stderr = stderr_f.read().decode("utf-8", errors="replace")
        return stdout, stderr, exit_code

    def run_many(self, commands: list[str], timeout: int = 60) -> list[Tuple[str, str, int]]:
        """Execute multiple commands sequentially and return all results."""
        return [self.run(cmd, timeout=timeout) for cmd in commands]

    # ------------------------------------------------------------------
    # File transfer (SFTP)
    # ------------------------------------------------------------------

    def get_file(self, remote_path: str) -> bytes:
        """Download *remote_path* and return its raw bytes."""
        import io
        sftp = self._client.open_sftp()
        buf = io.BytesIO()
        try:
            sftp.getfo(remote_path, buf)
        finally:
            sftp.close()
        return buf.getvalue()

    def put_file(self, data: bytes, remote_path: str) -> None:
        """Upload *data* to *remote_path* on the remote host."""
        import io
        sftp = self._client.open_sftp()
        try:
            sftp.putfo(io.BytesIO(data), remote_path)
        finally:
            sftp.close()

    def list_dir(self, remote_path: str) -> list[str]:
        """Return filenames in *remote_path* on the remote host."""
        sftp = self._client.open_sftp()
        try:
            return sftp.listdir(remote_path)
        except OSError:
            return []
        finally:
            sftp.close()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SshClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()
