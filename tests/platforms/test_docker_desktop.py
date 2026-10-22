"""Contract tests independent of Docker, plus opt-in real Xvfb acceptance."""
import asyncio
from contextlib import suppress
import io
import os
from pathlib import Path
import time
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image
from Xlib import X, XK, display, error

from wirehand.platforms.docker_desktop.backend import DockerDesktopBackend
from wirehand.platforms.docker_desktop.input import DesktopInput
from wirehand.platforms.docker_desktop.video import DesktopVideo
from wirehand.platforms.registry import load_backend


class DesktopContractTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.keysym_to_keycode.side_effect = lambda symbol: symbol
        self.connection.query_keymap.return_value = bytes(32)
        self.session = DesktopInput(self.connection)
        self.event = patch.object(self.session, '_event').start()
        self.addCleanup(patch.stopall)

    def test_invalid_requests_emit_no_events(self):
        actions = [
            lambda: self.session.keys(['ctrl', 'unknown']),
            lambda: self.session.keys(list('abcdefg')),
            lambda: self.session.press_key(['a'], 1001),
            lambda: self.session.press_key([]),
            lambda: self.session.type_text('valid prefix 中文'),
            lambda: self.session.type_text(''),
            lambda: self.session.pointer(float('nan'), .5),
            lambda: self.session.pointer(.5, .5, 1.5),
            lambda: self.session.pointer(.5, .5, wheel=128),
            lambda: self.session.click(-.1, .5),
            lambda: self.session.click(.5, .5, 'unknown'),
        ]
        for action in actions:
            with self.assertRaises(ValueError):
                action()
        self.event.assert_not_called()

    def test_modifiers_are_pressed_before_letters_even_in_browser_key_order(self):
        self.session.keys(['a', 'shift'])
        self.assertEqual(self.event.call_args_list[0].args, (X.KeyPress, XK.XK_Shift_L))
        self.assertEqual(self.event.call_args_list[1].args, (X.KeyPress, ord('a')))

    def test_release_all_attempts_pointer_even_if_keyboard_fails(self):
        with patch.object(self.session, '_set_codes', side_effect=OSError('keyboard failed')):
            with self.assertRaisesRegex(RuntimeError, 'keyboard failed'):
                self.session.release_all()
        self.assertEqual([call.args for call in self.event.call_args_list],
                         [(X.ButtonRelease, button) for button in range(1, 6)])

    def test_press_failure_still_releases_keyboard(self):
        with patch.object(self.session, '_set_codes', side_effect=[OSError('disconnected'), None]) as send:
            with self.assertRaises(OSError):
                self.session.press_key(['a'])
        self.assertEqual(send.call_args_list[-1].args, ([],))

    def test_pointer_coordinates_buttons_and_scroll_follow_contract(self):
        root = self.connection.screen().root
        root.get_geometry.return_value = type('Geometry', (), {'width': 1280, 'height': 800})()
        root.query_pointer.return_value.mask = 0
        self.session.pointer(1, 1, buttons=2, wheel=-1)
        self.assertEqual(self.event.call_args_list[0].kwargs, {'x': 1279, 'y': 799})
        self.assertEqual([call.args for call in self.event.call_args_list[1:]],
                         [(X.ButtonPress, 3), (X.ButtonPress, 5), (X.ButtonRelease, 5)])

    def test_factory_does_not_connect_to_display(self):
        with patch('wirehand.platforms.docker_desktop.backend.display.Display') as connect:
            backend = load_backend('docker-desktop', Path('/tmp/unused'))
            self.assertEqual(backend.name, 'docker-desktop')
            self.assertIsNone(backend.create_video().encoded)
            connect.assert_not_called()

    def test_missing_display_is_reported_without_crashing_status(self):
        with patch.dict(os.environ, DISPLAY=':99'), patch(
                'wirehand.platforms.docker_desktop.backend.display.Display',
                side_effect=error.DisplayConnectionError(':99', 'unavailable')):
            state = DockerDesktopBackend(Path('/tmp/unused')).status()
        self.assertFalse(state['input']['connected'])
        self.assertFalse(state['video']['signal'])
        self.assertIn(':99', state['video']['error'])

    def test_display_disconnect_during_capture_remains_a_recoverable_error(self):
        self.connection.screen().root.get_geometry.side_effect = error.ConnectionClosedError('server')
        self.connection.close.side_effect = error.ConnectionClosedError('server')
        with patch('wirehand.platforms.docker_desktop.backend.display.Display', return_value=self.connection):
            with self.assertRaisesRegex(RuntimeError, 'Desktop display'):
                DockerDesktopBackend(Path('/tmp/unused')).screenshot()


class DesktopVideoTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovers_capture_failure_skips_stale_frames_and_cancels(self):
        output = io.BytesIO()
        Image.new('RGB', (640, 400)).save(output, 'JPEG')
        backend = MagicMock()
        backend.screenshot.side_effect = [RuntimeError('X server restarting'), output.getvalue(), output.getvalue()]
        hub = DesktopVideo(backend)
        task = asyncio.create_task(hub.run())
        try:
            sequence, frame = await asyncio.wait_for(hub.next_frame(0), 3)
            self.assertEqual(frame, output.getvalue())
            self.assertTrue(hub.status()['signal'])
            next_sequence, _ = await asyncio.wait_for(hub.next_frame(sequence), 1)
            self.assertGreater(next_sequence, sequence)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self.assertFalse(hub.status()['signal'])
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(hub.next_frame(0), .05)


@unittest.skipUnless(os.environ.get('KVM_DESKTOP_LIVE_TEST') == '1', 'Requires isolated container Xvfb')
class DesktopLiveTests(unittest.TestCase):
    def test_real_capture_keyboard_pointer_and_cross_session_release(self):
        backend = DockerDesktopBackend(Path('/run/kvm-mcp'))
        connection = display.Display()
        self.addCleanup(connection.close)
        root = connection.screen().root
        window = root.create_window(0, 0, 320, 240, 0, connection.screen().root_depth,
                                    X.InputOutput, X.CopyFromParent,
                                    background_pixel=0xff0000, override_redirect=1,
                                    event_mask=X.KeyPressMask | X.KeyReleaseMask | X.ButtonPressMask)
        self.addCleanup(window.destroy)
        window.map()
        window.configure(stack_mode=X.Above)
        window.set_input_focus(X.RevertToParent, X.CurrentTime)
        connection.sync()
        # XTEST/X11 sync does not wait for the compositor to paint the window.
        deadline = time.monotonic() + 2
        while True:
            with Image.open(io.BytesIO(backend.screenshot(3840))) as frame:
                r, g, b = frame.getpixel((100, 100))
            if r > 230 or time.monotonic() >= deadline:
                break
            time.sleep(.02)
        self.assertGreater(r, 230)
        self.assertLess(g + b, 30)
        with Image.open(io.BytesIO(backend.screenshot(640))) as frame:
            self.assertEqual(frame.width, 640)
        with backend.open_input() as session:
            session.type_text('aA!\t\n')
            session.pointer(1, 1, 2)
            pointer = root.query_pointer()
            geometry = root.get_geometry()
            self.assertEqual((pointer.root_x, pointer.root_y), (geometry.width - 1, geometry.height - 1))
            self.assertTrue(pointer.mask & X.Button3Mask)
            session.keys(['ctrl', 'shift'])
            self.assertTrue(any(connection.query_keymap()))
        self.assertFalse(any(connection.query_keymap()))
        self.assertFalse(root.query_pointer().mask & (X.Button1Mask | X.Button2Mask | X.Button3Mask))
        # Inspect actual server events, not just calls to the translation layer.
        connection.sync()
        events = []
        while connection.pending_events():
            events.append(connection.next_event())
        letters = [(connection.keycode_to_keysym(event.detail, 0), bool(event.state & X.ShiftMask))
                   for event in events if event.type == X.KeyPress
                   and connection.keycode_to_keysym(event.detail, 0) in (ord('a'), ord('1'), XK.XK_Tab, XK.XK_Return)]
        self.assertEqual(letters, [(ord('a'), False), (ord('a'), True), (ord('1'), True),
                                  (XK.XK_Tab, False), (XK.XK_Return, False)])
        # A fresh session must recover a key held by an interrupted client.
        from Xlib.ext import xtest
        xtest.fake_input(connection, X.KeyPress, connection.keysym_to_keycode(XK.XK_Control_L))
        connection.sync()
        with backend.open_input() as session:
            session.release_all()
        self.assertFalse(any(connection.query_keymap()))


if __name__ == '__main__':
    unittest.main()
