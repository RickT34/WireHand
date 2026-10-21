"""Codec-neutral distribution mechanism; H.264 bytes are interpreted by consumers."""
from typing import Protocol


class EncodedSink(Protocol):
    def push(self, data: bytes, pts: int, keyframe: bool) -> None: ...
    def reset(self) -> None: ...


class EncodedHub:
    """Publish on the owning event loop. Consumers bound their own buffers."""

    def __init__(self):
        self.tracks: set[EncodedSink] = set()

    def publish(self, data: bytes, pts: int, keyframe: bool):
        for track in tuple(self.tracks):
            track.push(data, pts, keyframe)

    def reset(self):
        for track in tuple(self.tracks):
            track.reset()
