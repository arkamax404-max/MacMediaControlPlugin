import unittest
from unittest.mock import patch

from d200_bridge.media_adapter import create_media_adapter


class MediaAdapterFactoryTests(unittest.TestCase):
    def test_creates_generic_mediaremote_adapter(self):
        cache = object()
        with patch("d200_bridge.macos_mediaremote.MediaRemoteGateway") as gateway, patch(
            "d200_bridge.macos_media.MacOSCurrentMediaAdapter", return_value="mac"
        ) as adapter:
            self.assertEqual(create_media_adapter(cache), "mac")
        adapter.assert_called_once_with(cache, gateway=gateway.return_value)
