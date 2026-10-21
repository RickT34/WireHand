"""Fresh shared JPEG or standalone HDMI capture for ROCK 5B+."""
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from PIL import Image as PILImage

from kvm_mcp.runtime import exclusive_lock
from .device import find_video


def run(command: list[str], timeout: float = 5) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, 'LC_ALL': 'C'})
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'{command[0]} timed out; check HDMI signal/USB connection') from exc
    if result.returncode:
        raise RuntimeError((result.stderr + result.stdout)[-3000:].strip())
    return result.stdout


def screenshot(runtime: Path, max_width: int = 1280) -> bytes:
    """Capture a fresh HDMI frame as JPEG, preserving aspect ratio. Width 320..3840."""
    if not 320 <= max_width <= 3840:
        raise ValueError('max_width must be 320..3840')
    cached = runtime / 'frame.jpg'
    try:
        with cached.open('rb') as stream:
            if 0 <= time.time() - os.fstat(stream.fileno()).st_mtime < 2:
                data = stream.read()
                with PILImage.open(io.BytesIO(data)) as frame:
                    if frame.width > max_width:
                        frame.thumbnail((max_width, max_width * frame.height // frame.width))
                        output = io.BytesIO()
                        frame.save(output, 'JPEG', quality=85)
                        data = output.getvalue()
                return data
    except FileNotFoundError:
        pass
    with exclusive_lock(runtime, 'video'):
        device = find_video()
        timings = run(['v4l2-ctl', '-d', device, '--query-dv-timings'])
        width = re.search(r'Active width:\s*(\d+)', timings)
        height = re.search(r'Active height:\s*(\d+)', timings)
        if not width or not height:
            raise RuntimeError('Unable to read HDMI dimensions: ' + timings)
        source_w, source_h = int(width[1]), int(height[1])
        target_w = min(source_w, max_width)
        target_h = max(2, round(source_h * target_w / source_w / 2) * 2)
        if not Path('/dev/mpp_service').exists():
            run(['v4l2-ctl', '-d', device, '--set-dv-bt-timings=query'])
        with tempfile.TemporaryDirectory(prefix='kvm-frame-') as directory:
            frame = Path(directory) / 'frame.jpg'
            # GStreamer handles multiplanar V4L2 and per-plane stride.
            run(['gst-launch-1.0', '-q', 'v4l2src', f'device={device}', 'num-buffers=1',
                 'io-mode=mmap', '!', 'videoconvert', '!', 'videoscale', '!',
                 f'video/x-raw,width={target_w},height={target_h},pixel-aspect-ratio=1/1',
                 '!', 'jpegenc', 'quality=85', '!', 'filesink', f'location={frame}'], timeout=12)
            data = frame.read_bytes()
        if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
            raise RuntimeError('Capture did not produce a complete JPEG')
        return data


