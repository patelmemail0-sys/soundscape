"""Subprocess access funnelled through one object so tests can fake every external tool."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import IO, Any, Dict, Optional, Sequence, Tuple

from .errors import ToolMissing


@dataclass
class Result:
    argv: Tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        """stdout, or stderr when stdout is empty (pactl reports some things on stderr)."""
        return self.stdout if self.stdout.strip() else self.stderr


def _as_text(data: Any) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", "replace")
    return str(data)


class Runner:
    """Runs commands. Replace with a fake in tests."""

    def which(self, name: str) -> Optional[str]:
        return shutil.which(name)

    def require(self, name: str, hint: str = "") -> str:
        path = self.which(name)
        if not path:
            raise ToolMissing(name, hint)
        return path

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 30,
        input: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Result:
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout,
                input=input,
                env=env,
                check=False,
            )
        except FileNotFoundError:
            raise ToolMissing(str(argv[0])) from None
        except subprocess.TimeoutExpired as exc:
            return Result(tuple(argv), 124, _as_text(exc.stdout), f"timed out after {timeout}s")
        return Result(tuple(argv), proc.returncode, proc.stdout, proc.stderr)

    def popen(
        self,
        argv: Sequence[str],
        *,
        stdin: Optional[int] = None,
        stdout: Optional[Any] = None,
        stderr: Optional[Any] = None,
        env: Optional[Dict[str, str]] = None,
        text: bool = False,
        start_new_session: bool = False,
    ) -> subprocess.Popen[Any]:
        try:
            return subprocess.Popen(
                list(argv),
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                env=env,
                text=text,
                start_new_session=start_new_session,
            )
        except FileNotFoundError:
            raise ToolMissing(str(argv[0])) from None

    def open_log(self, path: str) -> IO[Any]:
        return open(path, "ab")
