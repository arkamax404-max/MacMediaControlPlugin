"""Platform-isolated factories for companion services."""

from __future__ import annotations

from pathlib import Path

class PosixFileLock:
    def __init__(self, path):
        self.path = Path(path)
        self.handle = None
        self.unavailable = False

    def acquire(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = None
        try:
            handle = open(self.path, "a+", encoding="ascii")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        except OSError:
            if handle is not None:
                handle.close()
            self.unavailable = True
            return False
        self.handle = handle
        return True

    def close(self):
        if self.handle is not None:
            import fcntl
            handle, self.handle = self.handle, None
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def create_lock(paths):
    return PosixFileLock(paths.config / "companion.lock")


def create_audio_controller(cache):
    from .macos_audio import MacOSOutputAudioController
    return MacOSOutputAudioController(cache)
