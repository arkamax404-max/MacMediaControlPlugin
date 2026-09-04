"""Generic current-media adapter for macOS MediaRemote data."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .artwork import artwork_processor as default_artwork_processor
from .macos_mediaremote import MediaRemoteGateway


class MacOSCurrentMediaAdapter:
    def __init__(self, cache, gateway=None, clock=None, artwork_processor=None):
        self.cache = cache
        self._gateway = gateway or MediaRemoteGateway()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._artwork_processor = artwork_processor or default_artwork_processor

    async def start(self):
        await self.refresh()

    async def stop(self):
        self.cache.unavailable()

    async def refresh(self):
        try:
            result = await asyncio.to_thread(self._gateway.read_now_playing)
            media = result.get("media") if isinstance(result, dict) else None
            if result.get("status") != "ok" or not isinstance(media, dict):
                self.cache.unavailable()
                return
            media = dict(media)
            artwork_id = self._artwork_id(media.pop("artwork", None))
            self.cache.update(self._state(media, artwork_id))
        except Exception:
            self.cache.unavailable()

    async def command(self, action):
        if action not in {"previous", "toggle", "next"}:
            raise ValueError("Unsupported command")
        return bool(await asyncio.to_thread(self._gateway.command, action))

    def _artwork_id(self, data):
        try:
            artwork = self._artwork_processor.process(data) if isinstance(data, bytes) else None
        except Exception:
            artwork = None
        return artwork.artwork_id if artwork is not None else None

    def _state(self, media, artwork_id):
        if media.get("state") not in {"playing", "paused", "stopped"}:
            raise ValueError("Unsupported media state")
        is_playing = media["state"] == "playing"
        duration = media["duration"]
        position = media["position"]
        rate = media.get("playback_rate", 1.0)
        return {"available": True, "is_playing": media["state"] == "playing", "title": media["title"],
                "artist": media["artist"], "artwork_id": artwork_id, "timeline_available": True,
                "duration_seconds": duration, "position_seconds": position,
                "playback_rate": rate,
                "position_updated_at": self._timeline_anchor(media.get("position_updated_at"), is_playing,
                                                              position, duration, rate)}

    def _timeline_anchor(self, source_timestamp, is_playing, position, duration, rate):
        source_anchor = self._valid_timestamp(source_timestamp)
        if source_anchor is not None:
            return source_anchor
        previous = self.cache.get()
        if (is_playing and previous.available and previous.timeline_available and previous.is_playing
                and (position, duration, rate) == (previous.position_seconds,
                                                   previous.duration_seconds,
                                                   previous.playback_rate)):
            return previous.position_updated_at
        return self._clock().isoformat()

    @staticmethod
    def _valid_timestamp(value):
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
