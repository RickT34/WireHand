"""One HDMI capture process; all viewers consume only its latest JPEG."""
import asyncio
from contextlib import ExitStack, suppress
import os
from pathlib import Path
import re
import time
import av

from kvm_automation.media import EncodedHub
from kvm_automation.runtime import exclusive_lock
from .device import find_video


async def command(*args):
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, 'LC_ALL': 'C'})
    try:
        out, err = await asyncio.wait_for(process.communicate(), 5)
    except BaseException:
        with suppress(ProcessLookupError):
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise RuntimeError((err + out).decode(errors='replace')[-1500:].strip())
    return out.decode()


async def read_frame(reader):
    header = await reader.readuntil(b'\r\n\r\n')
    match = re.search(rb'Content-Length:\s*(\d+)', header, re.I)
    if not match or not 4 <= int(match[1]) <= 16 * 1024 * 1024:
        raise RuntimeError('Invalid video frame length')
    data = await reader.readexactly(int(match[1]))
    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise RuntimeError('Incomplete JPEG')
    return data


def pipeline(source, width, height, mode='hardware', fps=60, dma_input=False):
    if not 1 <= fps <= 60:
        raise ValueError('KVM_MAX_FPS must be 1..60')
    args = ['gst-launch-1.0', '-q'] + source + [
        '!', 'queue', 'max-size-buffers=1', 'max-size-bytes=0', 'max-size-time=0', 'leaky=downstream',
        '!', 'videorate', 'drop-only=true', f'max-rate={fps}']
    if mode == 'hardware':
        args += ['!', 'v4l2convert', 'output-io-mode=' + ('dmabuf-import' if dma_input else 'mmap'), 'capture-io-mode=dmabuf',
                 '!', f'video/x-raw,format=NV12,width={width},height={height}',
                 '!', 'v4l2jpegenc', 'output-io-mode=dmabuf-import',
                 'extra-controls=s,compression_quality=80']
    elif mode == 'software':
        args += ['!', 'videoconvert', '!', 'videoscale',
                 '!', f'video/x-raw,width={width},height={height}', '!', 'jpegenc', 'quality=80']
    else:
        raise ValueError('KVM_VIDEO_MODE must be hardware or software')
    return args + ['!', 'multipartmux', 'boundary=frame', '!', 'fdsink', 'fd=1', 'sync=false']


def mpp_pipeline(device, input_width, input_height, width, height, fps=60):
    """Vendor kernel: V4L2 mmap capture, RGA conversion, MPP JPEG encoding."""
    if not 1 <= fps <= 60:
        raise ValueError('KVM_MAX_FPS must be 1..60')
    args = [os.environ.get('KVM_FFMPEG', '/usr/lib/jellyfin-ffmpeg/ffmpeg'),
            '-hide_banner', '-loglevel', 'warning', '-nostdin',
            '-init_hw_device', 'rkmpp=rk', '-filter_hw_device', 'rk']
    if device is None:
        args += ['-re', '-f', 'lavfi', '-i',
                 f'testsrc=size={input_width}x{input_height}:rate={fps},format=bgr24']
    else:
        args += ['-f', 'v4l2', '-input_format', 'bgr24',
                 '-video_size', f'{input_width}x{input_height}', '-i', device]
    return args + ['-an', '-vf',
                   f"select='isnan(prev_selected_t)+gt(floor(t*{fps}),floor(prev_selected_t*{fps}))',"
                   f'hwupload,scale_rkrga=w={width}:h={height}:format=nv12:async_depth=1',
                   '-c:v', 'mjpeg_rkmpp', '-qp_init', '80',
                   '-fps_mode', 'passthrough',
                   '-flush_packets', '1', '-f', 'mpjpeg', 'pipe:1']


def h264_pipeline(device, input_width, input_height, width, height, fps, bitrate, snapshot):
    args = mpp_pipeline(device, input_width, input_height, width, height, fps)
    args = args[:args.index('-an')]
    # The raw RGB upload remains a copy; RGA and both encoders share hardware frames.
    filters = (f"select='isnan(prev_selected_t)+gt(floor(t*{fps}),floor(prev_selected_t*{fps}))',"
               f'hwupload,scale_rkrga=w={width}:h={height}:format=nv12:async_depth=1,'
               'split=2[main][preview];[preview]fps=5[snapshot]')
    return args + ['-y', '-filter_complex', filters,
                   '-map', '[main]', '-an', '-c:v', 'h264_rkmpp',
                   '-profile:v', 'baseline', '-level:v', '4.2', '-coder', 'cavlc', '-8x8dct', '0',
                   '-bf', '0', '-g', str(max(1, fps // 2)), '-rc_mode', 'VBR',
                   '-b:v', str(bitrate), '-maxrate', str(bitrate * 4 // 3),
                   '-bufsize', str(bitrate // 6), '-fps_mode', 'passthrough',
                   '-bsf:v', 'h264_metadata=aud=insert', '-flush_packets', '1', '-f', 'h264', 'pipe:1',
                   '-map', '[snapshot]', '-an', '-c:v', 'mjpeg_rkmpp', '-qp_init', '80',
                   '-f', 'image2', '-update', '1', '-atomic_writing', '1', str(snapshot)]


class VideoHub:
    def __init__(self, runtime: Path):
        self.runtime = Path(runtime)
        self.frame = None
        self.sequence = 0
        self.updated = 0.0
        self.condition = asyncio.Condition()
        self.mode = os.environ.get('KVM_VIDEO_MODE', 'hardware')
        self.mpp = self.mode == 'hardware' and Path('/dev/mpp_service').exists()
        self.webrtc = self.mpp and os.environ.get('KVM_TRANSPORT', 'webrtc') == 'webrtc'
        self.encoded = EncodedHub() if self.webrtc else None
        self.bitrate = int(os.environ.get('KVM_H264_BITRATE', '6000000'))
        if not 500000 <= self.bitrate <= 20000000:
            raise ValueError('KVM_H264_BITRATE must be 500000..20000000')
        self.test_source = os.environ.get('KVM_VIDEO_SOURCE') == 'test'
        self.max_fps = int(os.environ.get('KVM_MAX_FPS', '60'))
        if not 1 <= self.max_fps <= 60:
            raise ValueError('KVM_MAX_FPS must be 1..60')
        self.info = {'signal': False, 'error': '等待 HDMI 信号', 'mode': self.mode,
                     'test_source': self.test_source, 'fps': 0, 'max_fps': self.max_fps,
                     'backend': 'rkmpp' if self.mpp else self.mode,
                     'transport': 'webrtc' if self.webrtc else 'jpeg',
                     'codec': 'H264' if self.webrtc else 'JPEG',
                     'bitrate_target': self.bitrate if self.webrtc else None,
                     'preview_fps': 5 if self.webrtc else self.max_fps,
                     'dma_input': self.mode == 'hardware' and not self.test_source and not self.mpp}
        self.process = None

    async def _read_snapshots(self):
        previous = None
        while True:
            try:
                path = self.runtime / 'frame.jpg'
                with path.open('rb') as stream:
                    stamp = os.fstat(stream.fileno()).st_mtime_ns
                    data = stream.read() if stamp != previous else None
                if data and data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
                    previous = stamp
                    self.frame = data
                    self.sequence += 1
                    async with self.condition:
                        self.condition.notify_all()
            except FileNotFoundError:
                pass
            await asyncio.sleep(.05)

    async def _read_h264(self, process):
        parser = av.CodecContext.create('h264', 'r')
        start, count, size = time.monotonic(), 0, 0
        last_pts = 0
        while True:
            chunk = await asyncio.wait_for(process.stdout.read(65536), 4)
            if not chunk:
                raise RuntimeError('H.264 encoder exited')
            for packet in parser.parse(chunk):
                now = time.monotonic()
                pts = max(last_pts + 1, round(now * 90000))
                last_pts = pts
                self.encoded.publish(bytes(packet), pts, packet.is_keyframe)
                self.updated = now
                self.info.update(signal=True, error='')
                count += 1
                size += packet.size
                if now - start >= 1:
                    self.info.update(fps=round(count / (now-start), 1),
                                     bitrate_actual=round(size * 8 / (now-start)))
                    start, count, size = now, 0, 0

    def status(self):
        age = time.monotonic() - self.updated
        return {**self.info, 'signal': self.info['signal'] and age < 2,
                'frame_age_ms': round(age * 1000) if self.updated else None}

    async def next_frame(self, after):
        async with self.condition:
            await self.condition.wait_for(lambda: self.sequence > after and self.frame is not None
                                          and time.monotonic() - self.updated < 2)
            return self.sequence, self.frame

    async def run(self):
        self.runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        while True:
            errors = bytearray()
            error_task = None
            snapshot_task = None
            resources = ExitStack()
            owns_capture = False
            try:
                # Serialize with the standalone MCP capture fallback.
                resources.enter_context(exclusive_lock(self.runtime, 'video', timeout=0))
                owns_capture = True
                if self.test_source:
                    width, height = 1920, 1080
                    device, input_width, input_height = None, width, height
                    source = ['videotestsrc', 'is-live=true', 'pattern=smpte', '!',
                              'video/x-raw,format=BGR,width=1920,height=1080,framerate=30/1']
                else:
                    device = find_video()
                    timings = await command('v4l2-ctl', '-d', device, '--query-dv-timings')
                    w = re.search(r'Active width:\s*(\d+)', timings)
                    h = re.search(r'Active height:\s*(\d+)', timings)
                    if not w or not h or not int(w[1]) or not int(h[1]):
                        raise RuntimeError('HDMI 尚未锁定有效画面')
                    scale = min(1, 1920 / int(w[1]), 1080 / int(h[1]))
                    width = max(96, int(int(w[1]) * scale) // 16 * 16)
                    height = max(32, int(int(h[1]) * scale) // 8 * 8)
                    # Vendor HDMI RX tracks timings itself and rejects S_DV_TIMINGS.
                    if not self.mpp:
                        await command('v4l2-ctl', '-d', device, '--set-dv-bt-timings=query')
                    formats = await command('v4l2-ctl', '-d', device, '--list-formats')
                    if "'BGR3'" not in formats:
                        raise RuntimeError('HDMI 正在输出非 RGB 格式；请检查 RGB EDID 配置或重新连接 HDMI')
                    # The driver can briefly report a stale format during HDMI
                    # negotiation. Pin both dimensions and the confirmed format.
                    await command('v4l2-ctl', '-d', device,
                                  f'--set-fmt-video=width={w[1]},height={h[1]},pixelformat=BGR3')
                    capture_io = 'dmabuf' if self.mode == 'hardware' else 'mmap'
                    source = ['v4l2src', f'device={device}', f'io-mode={capture_io}', 'do-timestamp=true',
                              '!', f'video/x-raw,format=BGR,width={w[1]},height={h[1]}']
                    self.info.update(input_width=int(w[1]), input_height=int(h[1]), input_format='BGR3')
                    input_width, input_height = int(w[1]), int(h[1])
                self.info.update(width=width, height=height)
                if self.webrtc:
                    args = h264_pipeline(device, input_width, input_height, width, height,
                                         self.max_fps, self.bitrate, self.runtime / 'frame.jpg')
                elif self.mpp:
                    args = mpp_pipeline(device, input_width, input_height,
                                        width, height, self.max_fps)
                else:
                    args = pipeline(source, width, height, self.mode, self.max_fps,
                                    self.mode == 'hardware' and not self.test_source)
                self.process = await asyncio.create_subprocess_exec(
                    *args, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE, limit=65536)

                process = self.process
                async def drain_errors():
                    while chunk := await process.stderr.read(4096):
                        errors.extend(chunk)
                        del errors[:-4000]

                error_task = asyncio.create_task(drain_errors())
                if self.webrtc:
                    snapshot_task = asyncio.create_task(self._read_snapshots())
                    await self._read_h264(process)
                start, count = time.monotonic(), 0
                while True:
                    data = await asyncio.wait_for(read_frame(self.process.stdout), 4)
                    now = time.monotonic()
                    self.frame, self.updated = data, now
                    self.sequence += 1
                    count += 1
                    self.info.update(signal=True, error='')
                    if now - start >= 1:
                        self.info['fps'] = round(count / (now - start), 1)
                        start, count = now, 0
                    # tmpfs cache lets MCP reuse capture without reopening HDMI.
                    temp = self.runtime / 'frame.tmp'
                    temp.write_bytes(data)
                    temp.replace(self.runtime / 'frame.jpg')
                    async with self.condition:
                        self.condition.notify_all()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc) or type(exc).__name__
                if errors:
                    message += ': ' + errors.decode(errors='replace')[-1800:]
                self.info.update(signal=False, error=message, fps=0)
            finally:
                try:
                    if self.encoded is not None:
                        self.encoded.reset()
                    if snapshot_task:
                        snapshot_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await snapshot_task
                    self.frame = None
                    if self.process:
                        with suppress(ProcessLookupError):
                            self.process.terminate()
                        try:
                            await asyncio.wait_for(self.process.wait(), 3)
                        except asyncio.TimeoutError:
                            with suppress(ProcessLookupError):
                                self.process.kill()
                            await self.process.wait()
                        self.process = None
                    if owns_capture:
                        (self.runtime / 'frame.jpg').unlink(missing_ok=True)
                    if error_task:
                        error_task.cancel()
                        with suppress(asyncio.CancelledError):
                            await error_task
                finally:
                    resources.close()
            await asyncio.sleep(2)
