import threading
import time
import uuid
from datetime import datetime, timezone

from .version import API_MAJOR, API_MINOR, COMPANION_VERSION


TRANSITIONS = {
    "starting": {"starting", "ready", "degraded", "stopping"},
    "ready": {"ready", "degraded", "stopping"},
    "degraded": {"degraded", "ready", "stopping"},
    "stopping": {"stopping"},
}


class CompanionLifecycle:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._started = clock()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._status = "starting"
        self._lock = threading.Lock()
        self.instance_id = str(uuid.uuid4())

    @property
    def status(self):
        with self._lock:
            return self._status

    def set_status(self, status):
        with self._lock:
            if status not in TRANSITIONS[self._status]:
                raise ValueError("Invalid lifecycle transition")
            self._status = status

    def health(self):
        return {
            "service": "d200-gsmtc-bridge",
            "companion_version": COMPANION_VERSION,
            "api_major": API_MAJOR,
            "api_minor": API_MINOR,
            "status": self.status,
            "instance_id": self.instance_id,
            "started_at": self._started_at,
            "uptime_seconds": round(max(0, self._clock() - self._started), 3),
        }
