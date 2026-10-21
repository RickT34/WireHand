"""Xvfb capture and XTEST input; container lifecycle belongs to Compose."""
from contextlib import contextmanager, suppress
import io
import os
from pathlib import Path

from PIL import Image
from Xlib import X, display, error

from .input import DesktopInput


class DockerDesktopBackend:
    name = 'docker-desktop'

    def __init__(self, runtime: Path):
        self.runtime = Path(runtime)
        self.display_name = os.environ.get('DISPLAY', ':99')

    @contextmanager
    def connection(self):
        connection = None
        try:
            connection = display.Display(self.display_name)
            if not connection.has_extension('XTEST'):
                raise RuntimeError('Desktop display requires the XTEST extension')
            yield connection
        except (error.DisplayError, error.XError, error.ConnectionClosedError) as exc:
            raise RuntimeError(f'Desktop display {self.display_name}: {exc}') from exc
        finally:
            if connection is not None:
                with suppress(error.ConnectionClosedError):
                    connection.close()

    def input_state(self):
        try:
            with self.connection():
                pass
            return {'connected': True, 'detail': f'XTEST on {self.display_name}'}
        except (OSError, RuntimeError) as exc:
            return {'connected': False, 'detail': str(exc)}

    def status(self):
        state = self.input_state()
        return {'platform': self.name, 'input': state,
                'video': {'signal': state['connected'],
                          'error': '' if state['connected'] else state['detail'],
                          'display': self.display_name}}

    def screenshot(self, max_width=1280):
        if type(max_width) is not int or not 320 <= max_width <= 3840:
            raise ValueError('max_width must be 320..3840')
        with self.connection() as connection:
            root = connection.screen().root
            geometry = root.get_geometry()
            formats = connection.display.info.pixmap_formats
            if geometry.depth != 24 or not any(
                    fmt.depth == 24 and fmt.bits_per_pixel == 32 for fmt in formats):
                raise RuntimeError('Desktop capture requires Xvfb with 24-bit depth and 32-bit pixels')
            reply = root.get_image(0, 0, geometry.width, geometry.height, X.ZPixmap, 0xffffffff)
            mode = 'BGRX' if connection.display.info.image_byte_order == X.LSBFirst else 'XRGB'
            image = Image.frombytes('RGB', (geometry.width, geometry.height), reply.data, 'raw', mode)
        if image.width > max_width:
            image = image.resize((max_width, max(1, round(image.height * max_width / image.width))))
        output = io.BytesIO()
        image.save(output, 'JPEG', quality=80)
        return output.getvalue()

    @contextmanager
    def open_input(self):
        with self.connection() as connection:
            session = DesktopInput(connection)
            try:
                yield session
            finally:
                session.release_all()

    def create_video(self):
        from .video import DesktopVideo
        return DesktopVideo(self)
