import asyncio
import io
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from PIL import Image
from kvm_rock5b_plus.video import VideoHub, read_frame, h264_pipeline, output_size
from kvm_automation.runtime import exclusive_lock
from kvm_rock5b_plus.hdmi import PROFILE, validate_edid
from kvm_rock5b_plus.snapshot import screenshot


class FrameTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_multipart_frames_preserve_boundaries(self):
        reader = asyncio.StreamReader()
        data = b'\xff\xd8test\xff\xd9'
        wire = b'--frame\r\nContent-Length: 8\r\n\r\n' + data + b'\r\n'

        async def feed():
            for chunk in [wire[:12], wire[12:34], wire[34:], wire]:
                reader.feed_data(chunk)
                await asyncio.sleep(0)
            reader.feed_eof()

        task = asyncio.create_task(feed())
        first, second = await read_frame(reader), await read_frame(reader)
        await task
        self.assertEqual((first, second), (data, data))

    async def test_unbounded_frame_is_rejected(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b'--frame\r\nContent-Length: 999999999\r\n\r\n')
        with self.assertRaisesRegex(RuntimeError, 'length'):
            await read_frame(reader)

    def test_mcp_uses_shared_frame_without_reopening_hdmi(self):
        with tempfile.TemporaryDirectory() as directory:
            Image.new('RGB', (1920,1080), (80,160,20)).save(Path(directory)/'frame.jpg')
            with patch('kvm_rock5b_plus.snapshot.run') as capture:
                result = screenshot(Path(directory), 640)
            capture.assert_not_called()
            with Image.open(io.BytesIO(result)) as image:
                self.assertEqual(image.size, (640,360))


class EDIDTests(unittest.TestCase):
    def test_profile_requests_1080p60_rgb(self):
        data = PROFILE.read_bytes()
        validate_edid(data)
        dtd = data[54:72]
        self.assertEqual(int.from_bytes(dtd[:2], 'little') * 10000, 148500000)
        self.assertEqual(dtd[2] + ((dtd[4] >> 4) << 8), 1920)
        self.assertEqual(dtd[5] + ((dtd[7] >> 4) << 8), 1080)
        self.assertEqual(data[128], 2)
        self.assertEqual(data[131] & 0x30, 0, 'YUV input must not be advertised')

    def test_damaged_profile_rejected_before_applying(self):
        data = bytearray(PROFILE.read_bytes())
        data[20] ^= 1
        with self.assertRaisesRegex(ValueError, 'checksum'):
            validate_edid(data)



class PipelineTests(unittest.TestCase):
    def test_remote_profile_caps_resolution_and_preserves_portrait_layout(self):
        self.assertEqual(output_size(1920,1080,1280,720),(1280,720))
        self.assertEqual(output_size(1080,1920,1280,720),(400,720))
        self.assertEqual(output_size(640,480,1280,720),(640,480))
        with patch.dict(os.environ,{},clear=True):
            hub=VideoHub(Path('/unused'))
            self.assertEqual((hub.max_width,hub.max_height,hub.max_fps,hub.bitrate),(1280,720,60,2500000))

    def test_720p60_pipeline_bounds_gop_and_bitrate_and_advertises_suitable_level(self):
        cmd=h264_pipeline('/dev/video0',1920,1080,1280,720,60,2500000,'/tmp/frame.jpg')
        self.assertEqual(cmd[cmd.index('-g')+1],'30')
        self.assertEqual(cmd[cmd.index('-level:v')+1],'3.2')
        self.assertEqual(cmd[cmd.index('-b:v')+1],'2500000')
        self.assertEqual(cmd[cmd.index('-maxrate')+1],'3333333')
        self.assertIn('scale_rkrga=w=1280:h=720',cmd[cmd.index('-filter_complex')+1])

    def test_vendor_pipeline_uses_hw_encoder_without_b_frames(self):
        cmd = h264_pipeline('/dev/video0',1920,1080,1920,1080,60,6000000,'/tmp/frame.jpg')
        self.assertIn('h264_rkmpp',cmd)
        self.assertNotIn('libx264',cmd)
        self.assertEqual(cmd[cmd.index('-bf')+1],'0')
        self.assertIn('mjpeg_rkmpp',cmd)


class CaptureLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_busy_capture_does_not_remove_other_process_snapshot(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, KVM_VIDEO_MODE='software'):
            runtime = Path(directory)
            cache = runtime / 'frame.jpg'
            cache.write_bytes(b'other-process-frame')
            hub = VideoHub(runtime)
            with exclusive_lock(runtime, 'video'):
                task = asyncio.create_task(hub.run())
                await asyncio.sleep(0)  # Run through the failed nonblocking lock acquisition.
                self.assertIn('busy', hub.status()['error'])
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(cache.read_bytes(), b'other-process-frame')

    async def test_cancelled_capture_reaps_process_and_releases_lock_and_cache(self):
        # Real subprocess/pipe/cancellation; only replace the device command.
        program = ("import sys, signal; "
                   "sys.stdout.buffer.write(b'--frame\\r\\nContent-Length: 8\\r\\n\\r\\n\\xff\\xd8test\\xff\\xd9\\r\\n'); "
                   "sys.stdout.flush(); signal.pause()")
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, KVM_VIDEO_MODE='software', KVM_VIDEO_SOURCE='test'
        ), patch('kvm_rock5b_plus.video.pipeline', return_value=[sys.executable, '-c', program]):
            runtime = Path(directory)
            hub = VideoHub(runtime)
            task = asyncio.create_task(hub.run())
            try:
                _, frame = await asyncio.wait_for(hub.next_frame(-1), 5)
                self.assertEqual(frame, b'\xff\xd8test\xff\xd9')
                process = hub.process
                with self.assertRaisesRegex(RuntimeError, 'busy'):
                    with exclusive_lock(runtime, 'video', timeout=0):
                        self.fail('Running capture lost its lock')
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertIsNotNone(process.returncode)
            self.assertFalse((runtime / 'frame.jpg').exists())
            with exclusive_lock(runtime, 'video', timeout=0):
                pass
