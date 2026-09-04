"""Privacy-safe boundary around the packaged macOS MediaRemote helper."""

from __future__ import annotations

import base64
import json
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT_SECONDS = 3.0
COMMANDS = frozenset(("toggle", "next", "previous"))
DIAGNOSTIC_STAGES = frozenset(("launch", "timeout", "exit", "parse", "schema"))


class MediaRemoteGateway:
    """Read current media and send generic transport through a native helper.

    Diagnostics deliberately retain only a bounded failure stage and a process
    exit code. They never retain helper output, media metadata, artwork, tokens,
    or paths.
    """

    def __init__(self, runner: Callable[..., Any] = subprocess.run,
                 timeout=DEFAULT_TIMEOUT_SECONDS, helper_path=None):
        self._runner = runner
        self._timeout = timeout
        self._helper_path = Path(helper_path) if helper_path else self._default_helper_path()
        self.last_diagnostic = None

    def read_now_playing(self):
        completed = self._invoke("read")
        if completed is None:
            return self._unavailable()
        payload = self._parse(completed)
        if payload is None:
            return self._unavailable()
        media = payload.get("media")
        if payload.get("status") != "ok" or not self._valid_media(media):
            self._diagnose("schema")
            return self._unavailable()
        media = dict(media)
        artwork = media.pop("artwork", None)
        try:
            media["artwork"] = base64.b64decode(artwork, validate=True) if isinstance(artwork, str) else None
        except ValueError:
            self._diagnose("schema")
            return self._unavailable()
        return {"status": "ok", "media": media}

    def command(self, action):
        if action not in COMMANDS:
            raise ValueError("Unsupported command")
        completed = self._invoke(action)
        if completed is None:
            return False
        payload = self._parse(completed)
        if payload is None:
            return False
        if payload == {"status": "ok"}:
            return True
        if payload == {"status": "rejected"}:
            return False
        self._diagnose("schema")
        return False

    def _invoke(self, operation):
        try:
            completed = self._runner([str(self._helper_path), operation], capture_output=True,
                                     text=True, timeout=self._timeout, check=False)
        except subprocess.TimeoutExpired:
            self._diagnose("timeout")
            return None
        except (FileNotFoundError, OSError):
            self._diagnose("launch")
            return None
        if completed.returncode:
            self._diagnose("exit", completed.returncode)
            return None
        return completed

    def _parse(self, completed):
        try:
            payload = json.loads(completed.stdout)
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            self._diagnose("parse")
            return None
        return payload if isinstance(payload, dict) else self._schema_error()

    def _schema_error(self):
        self._diagnose("schema")
        return None

    def _diagnose(self, stage, exit_code=None):
        if stage not in DIAGNOSTIC_STAGES:
            raise ValueError("Unsupported diagnostic stage")
        code = self._safe_exit_code(exit_code)
        self.last_diagnostic = {"stage": stage, "exit_code": code}
        logging.getLogger("d200_bridge").info("mediaremote_failure", stage, code if code is not None else "none")

    @staticmethod
    def _safe_exit_code(value):
        return value if isinstance(value, int) and 0 <= value <= 255 else None

    @staticmethod
    def _default_helper_path():
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).parent / "native"
        return root / "MediaRemoteHelper"

    @staticmethod
    def _valid_media(media):
        required = {"state", "title", "artist", "duration", "position", "artwork"}
        optional = {"playback_rate", "position_updated_at"}
        return (isinstance(media, dict) and required <= set(media) <= required | optional
                and media.get("state") in {"playing", "paused", "stopped"}
                and isinstance(media.get("title"), str) and isinstance(media.get("artist"), str)
                and isinstance(media.get("duration"), (int, float))
                and isinstance(media.get("position"), (int, float))
                and ("playback_rate" not in media or isinstance(media["playback_rate"], (int, float)))
                and ("position_updated_at" not in media or isinstance(media["position_updated_at"], str))
                and (isinstance(media.get("artwork"), str) or media.get("artwork") is None))

    @staticmethod
    def _unavailable():
        return {"status": "unavailable", "media": None}
