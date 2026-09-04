"""macOS global-output volume and mute controller."""

from dataclasses import dataclass
import re
import subprocess


VOLUME_STEP_PERCENT = 5
_AUDIO_STATE = re.compile(r"^(0|[1-9][0-9]?|100)\|(true|false)$")


@dataclass(frozen=True)
class AudioCommandResult:
    status: str
    applied_count: int
    failed_count: int
    state: dict

    @property
    def ok(self): return self.status == "ok"

    def public(self):
        return {"ok": self.ok, "status": self.status, "applied_count": self.applied_count,
                "failed_count": self.failed_count, **self.state}


class MacOSOutputAudioGateway:
    def __init__(self, runner=subprocess.run, timeout=3.0):
        self._runner, self._timeout = runner, timeout

    def read_state(self):
        result = self._run('set volumeSettings to get volume settings\n'
                           'set outputVolume to output volume of volumeSettings\n'
                           'if outputVolume < 0 then set outputVolume to 0\n'
                           'if outputVolume > 100 then set outputVolume to 100\n'
                           'set mutedText to "false"\n'
                           'if output muted of volumeSettings then set mutedText to "true"\n'
                           'return (outputVolume as integer as text) & "|" & mutedText')
        if result.returncode:
            return None
        state = _AUDIO_STATE.fullmatch(result.stdout.strip())
        if state is None:
            return None
        return {"volume_percent": int(state.group(1)), "is_muted": state.group(2) == "true"}

    def set_volume(self, percent):
        return self._run(f"set volume output volume {max(0, min(100, int(percent)))}").returncode == 0

    def set_muted(self, muted):
        return self._run(f"set volume output muted {'true' if muted else 'false'}").returncode == 0

    def _run(self, script):
        return self._runner(["/usr/bin/osascript", "-e", script], capture_output=True, text=True,
                            timeout=self._timeout, check=False)


class MacOSOutputAudioController:
    """Publishes and changes the one macOS system output, never app session audio."""
    def __init__(self, cache, gateway=None):
        self.cache, self._gateway = cache, gateway or MacOSOutputAudioGateway()

    def refresh(self):
        state = self._read()
        if state is None:
            self.cache.audio_unavailable()
            return False
        self.cache.update_audio(state)
        return True

    def command(self, action):
        if action not in {"volume-up", "volume-down", "mute-toggle"}:
            raise ValueError("Unsupported audio command")
        state = self._read()
        if state is None:
            return self._result("failed", 0, 1)
        try:
            applied = self._gateway.set_muted(not state["is_muted"]) if action == "mute-toggle" else self._gateway.set_volume(
                max(0, min(100, state["volume_percent"] + (VOLUME_STEP_PERCENT if action == "volume-up" else -VOLUME_STEP_PERCENT))))
        except (OSError, ValueError, subprocess.SubprocessError):
            applied = False
        if not applied:
            self.cache.audio_unavailable()
            return self._result("failed", 0, 1)
        verified = self._read()
        if verified is None:
            self.cache.audio_unavailable()
            return self._result("failed", 0, 1)
        self.cache.update_audio(verified)
        return self._result("ok", 1, 0)

    def stop(self):
        self.cache.audio_unavailable()

    def _read(self):
        try:
            raw = self._gateway.read_state()
            if not isinstance(raw, dict): return None
            return {"audio_available": True, "volume_percent": max(0, min(100, int(raw["volume_percent"]))),
                    "is_muted": bool(raw["is_muted"]), "audio_session_count": 1, "audio_mixed": False}
        except (KeyError, OSError, ValueError, subprocess.SubprocessError):
            return None

    def _result(self, status, applied, failed):
        state = self.cache.get().public()
        return AudioCommandResult(status, applied, failed, {key: state[key] for key in (
            "audio_available", "volume_percent", "is_muted", "audio_session_count", "audio_mixed")})
