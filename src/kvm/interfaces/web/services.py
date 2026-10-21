"""State owned by one web application instance and shared by its routes."""
import asyncio
from collections import deque
from dataclasses import dataclass, field

from kvm.core.contracts import Backend, VideoSource
from .auth import SessionAuth
from .control import Controller
from .video.h264 import H264Relay
from .video.rtc import RTCManager


@dataclass
class WebServices:
    auth: SessionAuth
    backend: Backend
    hub: VideoSource
    controller: Controller
    rtc: RTCManager | None
    h264: H264Relay | None
    control_timeout: float = 7
    control_mutex: asyncio.Lock = field(default_factory=asyncio.Lock)
    rtc_reports: deque = field(default_factory=lambda: deque(maxlen=20))
