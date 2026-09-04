from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from bridge_client import BridgeClient, BridgeHealthResult
from transport_diagnostics import CompanionStartupDiagnosticFile


READINESS_TIMEOUT_SECONDS = 5.0
READINESS_POLL_SECONDS = 0.1
STOP_WAIT_SECONDS = 3.0
MAX_STDERR_BYTES = 512
_SAFE_STDERR_MARKERS = ((b"startup_failed", "startup_failed"),)


@dataclass(frozen=True)
class CompanionStartResult:
    status: str
    owned: bool
    stage: str | None = None
    exit_code: int | None = None
    stderr: str = ""

    @property
    def ready(self) -> bool:
        return self.status == "ready"


class CompanionSupervisor:
    """Starts one bundled bridge only when authenticated health is unavailable."""

    def __init__(
        self,
        client_factory: Callable[[], BridgeClient] = BridgeClient,
        popen: Callable = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        executable: str | None = None,
        readiness_timeout: float = READINESS_TIMEOUT_SECONDS,
        readiness_poll: float = READINESS_POLL_SECONDS,
        stop_wait: float = STOP_WAIT_SECONDS,
        logger: logging.Logger | None = None,
        startup_diagnostic_file: CompanionStartupDiagnosticFile | None = None,
    ) -> None:
        self._client_factory, self._popen = client_factory, popen
        self._clock, self._sleep = clock, sleep
        self._executable = executable or sys.executable
        self._readiness_timeout, self._readiness_poll, self._stop_wait = (
            readiness_timeout, readiness_poll, stop_wait
        )
        self._child = None
        self._owned_instance_id: str | None = None
        self._logger = logger or logging.getLogger("ulanzi_runtime")
        self._startup_diagnostic_file = startup_diagnostic_file

    def ensure_ready(self) -> CompanionStartResult:
        if self._probe().status == "compatible":
            return CompanionStartResult("ready", False)
        try:
            child = self._popen(
                [self._executable, "--d200-bridge"], shell=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
        except (OSError, ValueError):
            return self._failure("spawned")
        stderr = _RedactedStderrBuffer()
        stderr_reader = self._start_stderr_reader(child, stderr)
        deadline = self._clock() + self._readiness_timeout
        while self._clock() < deadline:
            exit_code = child.poll()
            if exit_code is not None:
                if stderr_reader is not None:
                    stderr_reader.join(timeout=0.05)
                return self._failure("exited", exit_code, stderr.snapshot())
            health = self._probe()  # Create a client each pass so a new token is reloaded.
            if health.status == "compatible" and health.instance_id:
                self._child = child
                self._owned_instance_id = health.instance_id
                return CompanionStartResult("ready", True)
            self._sleep(self._readiness_poll)
        return self._failure("health-timeout", stderr=stderr.snapshot())

    def shutdown(self) -> None:
        child, instance_id = self._child, self._owned_instance_id
        self._child = self._owned_instance_id = None
        if child is None or instance_id is None:
            return
        try:
            self._client_factory().stop_owned(instance_id)
        except Exception:
            return
        try:
            child.wait(timeout=self._stop_wait)
        except (subprocess.TimeoutExpired, OSError):
            pass

    def _probe(self) -> BridgeHealthResult:
        try:
            return self._client_factory().probe_health()
        except Exception:
            return BridgeHealthResult("unavailable")

    def _start_stderr_reader(self, child, buffer: "_RedactedStderrBuffer") -> None:
        stream = getattr(child, "stderr", None)
        if stream is None:
            return None
        reader = threading.Thread(target=buffer.drain, args=(stream,), name="d200-stderr", daemon=True)
        reader.start()
        return reader

    def _failure(
        self, stage: str, exit_code=None, stderr: str = ""
    ) -> CompanionStartResult:
        result = CompanionStartResult(
            "companion_start_failed", False, stage, _safe_exit_code(exit_code), stderr
        )
        self._persist_failure(result)
        self._logger.info(
            "companion_start_failure stage=%s exit_code=%s stderr=%s",
            result.stage,
            result.exit_code if result.exit_code is not None else "unavailable",
            result.stderr or "none",
        )
        return result

    def _persist_failure(self, result: CompanionStartResult) -> None:
        try:
            diagnostic_file = self._startup_diagnostic_file or CompanionStartupDiagnosticFile()
            diagnostic_file.write(result.stage, result.exit_code, result.stderr or "none")
        except (KeyError, OSError, ValueError):
            # Diagnostics must never change companion startup lifecycle behavior.
            pass


def _safe_exit_code(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 255 else None


class _RedactedStderrBuffer:
    """Drain child stderr without retaining child-controlled output."""

    def __init__(self, maximum: int = MAX_STDERR_BYTES) -> None:
        self._maximum = maximum
        self._seen = 0
        self._markers: set[str] = set()
        self._lock = threading.Lock()

    def drain(self, stream) -> None:
        while True:
            try:
                chunk = stream.read(256)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", "replace")
            with self._lock:
                retained = chunk[:max(0, self._maximum - self._seen)]
                self._seen += len(retained)
                for marker, category in _SAFE_STDERR_MARKERS:
                    if marker in retained:
                        self._markers.add(category)

    def snapshot(self) -> str:
        with self._lock:
            if not self._seen:
                return ""
            return ",".join(sorted(self._markers)) or "redacted"
