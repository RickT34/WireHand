"""Bounded, latest-frame JPEG preview for the container desktop."""
import asyncio
import io
import time

from PIL import Image


class DesktopVideo:
    encoded = None

    def __init__(self, backend):
        self.backend = backend
        self.condition = asyncio.Condition()
        self.sequence = 0
        self.frame = None
        self.updated = 0
        self.info = {'signal': False, 'error': 'Waiting for desktop', 'fps': 5}

    def status(self):
        return dict(self.info)

    async def next_frame(self, after):
        async with self.condition:
            await self.condition.wait_for(lambda: self.frame is not None and self.sequence > after
                                          and time.monotonic() - self.updated < 2)
            return self.sequence, self.frame

    async def run(self):
        try:
            while True:
                started = time.monotonic()
                try:
                    frame = await asyncio.to_thread(self.backend.screenshot, 1280)
                    with Image.open(io.BytesIO(frame)) as image:
                        width, height = image.size
                    async with self.condition:
                        self.frame = frame
                        self.sequence += 1
                        self.updated = time.monotonic()
                        self.info.update(signal=True, error='', width=width, height=height)
                        self.condition.notify_all()
                except (OSError, RuntimeError) as exc:
                    self.frame = None
                    self.info.update(signal=False, error=str(exc))
                    await asyncio.sleep(1)
                await asyncio.sleep(max(.01, .2 - (time.monotonic() - started)))
        finally:
            self.frame = None
            self.info.update(signal=False, error='Desktop preview stopped')
