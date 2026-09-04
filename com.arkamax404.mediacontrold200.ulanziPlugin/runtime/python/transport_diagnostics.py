"""Bounded, category-only diagnostics for physical transport controls."""

from __future__ import annotations

import json
import logging
import os
import secrets
import stat
from collections import Counter
from pathlib import Path


MAX_COUNTER = 1_000_000
PHYSICAL_TRANSPORT_EVENTS = frozenset(("previous", "toggle", "next"))
RESULT_CLASSES = frozenset(("success", "rejected", "unavailable", "compatibility", "lifecycle"))
PRODUCT_DIRECTORY = "GSMTCD200Controller"
DIAGNOSTIC_FILENAME = "transport-counters.json"


def result_class(status: str) -> str:
    if status == "ok":
        return "success"
    if status == "rejected":
        return "rejected"
    if status in {"configuration", "incompatible"}:
        return "compatibility"
    if status in {"stopped", "queue_full", "discarded"}:
        return "lifecycle"
    return "unavailable"


def transport_diagnostic_path(home=None) -> Path:
    """Return the user-owned companion diagnostics file path."""
    selected_home = Path(home if home is not None else os.environ["HOME"])
    if not selected_home.is_absolute():
        raise ValueError("HOME must be an absolute path")
    return (selected_home / "Library" / "Logs" / PRODUCT_DIRECTORY / "diagnostics"
            / DIAGNOSTIC_FILENAME)


def _validate_directory(path: Path) -> None:
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise OSError("Unsafe diagnostics directory")


class CategoryCounterFile:
    """Atomically persists the fixed counter snapshot without request-derived data."""

    def __init__(self, path=None, home=None) -> None:
        self.path = Path(path) if path is not None else transport_diagnostic_path(home)
        if not self.path.is_absolute():
            raise ValueError("Diagnostics path must be absolute")

    def write(self, counters: dict[str, int]) -> None:
        directory = self.path.parent
        self._ensure_directory_chain(directory)
        try:
            metadata = os.lstat(self.path)
        except FileNotFoundError:
            pass
        else:
            if (stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1):
                raise OSError("Unsafe diagnostics file")

        payload = json.dumps(counters, sort_keys=True, separators=(",", ":")) + "\n"
        temporary = directory / f".{self.path.name}.{secrets.token_hex(8)}.tmp"
        descriptor = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="ascii", newline="") as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory_descriptor = os.open(
                directory, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _ensure_directory_chain(directory: Path) -> None:
        missing = []
        candidate = directory
        while not candidate.exists():
            missing.append(candidate)
            candidate = candidate.parent
        _validate_directory(candidate)
        for candidate in reversed(missing):
            candidate.mkdir(mode=0o700)
            _validate_directory(candidate)


class TransportDiagnostics:
    """Records only fixed diagnostic categories; it never accepts request data."""

    def __init__(self, logger=None, counter_file=None) -> None:
        self._logger = logger or logging.getLogger("ulanzi_transport")
        self._counters = Counter()
        self._counter_file = counter_file

    def recognized_event(self, action: str) -> None:
        if action in PHYSICAL_TRANSPORT_EVENTS:
            self._record(f"recognized_{action}")

    def command_post_attempt(self) -> None:
        self._record("command_post_attempt")

    def result(self, status: str) -> None:
        self._record(f"result_{result_class(status)}")

    def snapshot(self) -> dict[str, int]:
        return {name: self._counters[name] for name in sorted(self._counters)}

    def _record(self, category: str) -> None:
        self._counters[category] = min(MAX_COUNTER, self._counters[category] + 1)
        self._logger.info("transport_diagnostic category=%s count=%d", category,
                          self._counters[category])
        if self._counter_file is not None:
            try:
                self._counter_file.write(self.snapshot())
            except OSError:
                # Diagnostics must never interfere with transport control.
                pass


def configure_transport_diagnostics_logging() -> None:
    """Emit fixed category records to the plugin's inherited local stderr log."""
    logger = logging.getLogger("ulanzi_transport")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
