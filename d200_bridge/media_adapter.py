"""Platform selection for media metadata adapters."""


def create_media_adapter(cache):
    from .macos_media import MacOSCurrentMediaAdapter
    from .macos_mediaremote import MediaRemoteGateway
    return MacOSCurrentMediaAdapter(cache, gateway=MediaRemoteGateway())
