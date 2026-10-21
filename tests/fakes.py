"""A semantic backend for application tests; intentionally no platform imports."""
import asyncio
from contextlib import contextmanager
import io
import threading

from PIL import Image


class FakeInput:
    def __init__(self, backend):
        self.backend = backend
        self.position = (0, 0)

    def keys(self, keys):
        self.backend.events.append(('keys', keys))

    def pointer(self, x, y, buttons=0, wheel=0):
        self.backend.events.append(('pointer', x, y, buttons, wheel))
        self.position = (x, y)

    def press_key(self, keys, hold_ms=50):
        self.backend.events.append(('press_key', keys, hold_ms))

    def type_text(self, text):
        self.backend.events.append(('type_text', text))

    def click(self, x, y, button='none', wheel=0):
        self.backend.events.append(('click', x, y, button, wheel))

    def release_all(self):
        self.keys([])
        self.pointer(*self.position)
        if self.backend.fail_release:
            raise OSError('Input disconnected')


class FakeVideo:
    encoded = None

    def __init__(self):
        self.started = False
        self.stopped = False

    def status(self):
        return {'signal': False, 'error': 'No signal', 'transport': 'jpeg'}

    async def run(self):
        self.started = True
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped = True

    async def next_frame(self, after):
        return after + 1, jpeg(640)


def jpeg(width):
    stream = io.BytesIO()
    Image.new('RGB', (width, width * 9 // 16), (80, 160, 20)).save(stream, 'JPEG')
    return stream.getvalue()


class FakeBackend:
    name = 'test-platform'

    def __init__(self, runtime=None):
        self.events = []
        self.video = FakeVideo()
        self.fail_release = False
        self.opened = 0
        self.closed = 0
        self.closed_event = threading.Event()

    def input_state(self):
        return {'connected': True, 'detail': 'test input'}

    def status(self):
        return {'platform': self.name, 'input': self.input_state(), 'video': self.video.status()}

    def screenshot(self, max_width=1280):
        return jpeg(max_width)

    @contextmanager
    def open_input(self):
        self.opened += 1
        try:
            yield FakeInput(self)
        finally:
            self.closed += 1
            self.closed_event.set()

    def create_video(self):
        return self.video
