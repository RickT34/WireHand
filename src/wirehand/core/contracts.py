"""The boundary between applications and platform adapters.

Adapters own device discovery, capture, encoding and input translation. They
never implement authentication or human/MCP ownership. All calls except video
streaming are synchronous and may block; async callers must use a worker thread.
"""
from pathlib import Path
from typing import Any, ContextManager, Protocol, TypedDict

from wirehand.core.media import EncodedHub


class InputStatus(TypedDict):
    connected: bool
    detail: str


class InputSession(Protocol):
    """One open input connection. Closing it frees resources.

    keys/pointer represent held state. press_key/type_text/click release even on
    failure. Invalid requests must not emit input. release_all attempts both
    keyboard and pointer release before raising any error.
    """

    def keys(self, keys: list[str]) -> None: ...
    def pointer(self, x: float, y: float, buttons: int = 0, wheel: int = 0) -> None: ...
    def press_key(self, keys: list[str], hold_ms: int = 50) -> None: ...
    def type_text(self, text: str) -> None: ...
    def click(self, x: float, y: float, button: str = 'none', wheel: int = 0) -> None: ...
    def release_all(self) -> None: ...


class VideoSource(Protocol):
    """Latest JPEG plus optional H.264 Annex-B stream (90 kHz timestamps).

    run() owns capture until cancelled and must clean up devices/subprocesses.
    next_frame() waits for a fresh JPEG newer than the caller's sequence.
    status() includes signal/error, with optional dimensions, fps and diagnostics.
    encoded=None means JPEG only; packet publication occurs on the event loop.
    """

    encoded: EncodedHub | None

    def status(self) -> dict[str, Any]: ...
    async def run(self) -> None: ...
    async def next_frame(self, after: int) -> tuple[int, bytes]: ...


class Backend(Protocol):
    """A factory accepts runtime: Path; it must not open hardware at import time.

    Web and MCP processes for one target must share the runtime directory.
    screenshot returns a fresh JPEG, preserving aspect ratio with width <= the
    requested maximum. Adapter diagnostics live in status(), not app policy.
    """

    name: str

    def status(self) -> dict[str, Any]: ...
    def input_state(self) -> InputStatus: ...
    def screenshot(self, max_width: int = 1280) -> bytes: ...
    def open_input(self) -> ContextManager[InputSession]: ...
    def create_video(self) -> VideoSource: ...


class BackendFactory(Protocol):
    def __call__(self, runtime: Path) -> Backend: ...
