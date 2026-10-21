"""Application integration with a platform that has no device implementation."""
import asyncio
import base64
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image
from starlette.testclient import TestClient

from kvm.interfaces.web.control import Controller
from kvm.interfaces.mcp.server import create_mcp
from kvm.core.ownership import InputGate
from kvm.interfaces.web.app import make_app
from tests.fakes import FakeBackend


class MCPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tools_forward_semantic_input_and_close_each_session(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            server = create_mcp(backend, InputGate(Path(directory)))
            await server.call_tool('press_key', {'keys': ['ctrl', 'a'], 'hold_ms': 100})
            await server.call_tool('type_text', {'text': 'hello'})
            await server.call_tool('mouse', {'x': .25, 'y': .75, 'button': 'left', 'wheel': -1})
            await server.call_tool('release_all', {})
        self.assertEqual(backend.events, [
            ('press_key', ['ctrl', 'a'], 100), ('type_text', 'hello'),
            ('click', .25, .75, 'left', -1), ('keys', []), ('pointer', 0, 0, 0, 0)])
        self.assertEqual(backend.opened, 4)
        self.assertEqual(backend.closed, 4)

    async def test_stdio_tools_and_cross_process_human_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = InputGate(Path(directory))
            controller = Controller(FakeBackend(), gate)
            owner = object()
            controller.claim(owner)
            self.addCleanup(controller.disconnect, owner)
            params = StdioServerParameters(command=sys.executable, args=[
                '-m', 'kvm', 'mcp', '--backend', 'tests.fakes:FakeBackend',
                '--runtime', directory], cwd=str(Path(__file__).resolve().parents[2]))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    self.assertEqual({tool.name for tool in (await client.list_tools()).tools},
                                     {'status', 'screenshot', 'press_key', 'type_text', 'mouse', 'release_all',
                                      'inspect_screen', 'inspect_region', 'click_target'})
                    state = await client.call_tool('status', {})
                    self.assertFalse(state.isError)
                    self.assertTrue(json.loads(state.content[0].text)['manual_control'])
                    frame = await client.call_tool('screenshot', {'max_width': 640})
                    self.assertFalse(frame.isError)
                    with Image.open(io.BytesIO(base64.b64decode(frame.content[0].data))) as image:
                        self.assertEqual(image.size, (640, 360))
                    invalid = await client.call_tool('screenshot', {'max_width': 1})
                    self.assertTrue(invalid.isError)
                    inspection = await client.call_tool('inspect_screen', {})
                    self.assertFalse(inspection.isError, inspection)
                    self.assertEqual([item.type for item in inspection.content], ['text', 'image'])
                    observation = json.loads(inspection.content[0].text)
                    grid = await client.call_tool('inspect_region', {
                        'snapshot_id': observation['snapshot_id'], 'region_id': 'FULL', 'mode': 'grid'})
                    self.assertFalse(grid.isError, grid)
                    cells = json.loads(grid.content[0].text)
                    self.assertGreater(len(grid.content), 1)
                    denied = await client.call_tool('click_target', {
                        'snapshot_id': observation['snapshot_id'], 'target_id': cells['targets'][0]['id']})
                    self.assertTrue(denied.isError)
                    self.assertIn('manual control', denied.content[0].text)
                    for name, args in [('press_key', {'keys': ['enter']}),
                                       ('type_text', {'text': 'test'}),
                                       ('mouse', {'x': .5, 'y': .5}), ('release_all', {})]:
                        result = await client.call_tool(name, args)
                        self.assertTrue(result.isError, name)
                        self.assertIn('manual control', result.content[0].text)
                    controller.give_back(owner)
                    inspection = await client.call_tool('inspect_screen', {})
                    observation = json.loads(inspection.content[0].text)
                    grid = await client.call_tool('inspect_region', {
                        'snapshot_id': observation['snapshot_id'], 'region_id': 'FULL', 'mode': 'grid'})
                    cells = json.loads(grid.content[0].text)
                    clicked = await client.call_tool('click_target', {
                        'snapshot_id': observation['snapshot_id'], 'target_id': cells['targets'][0]['id']})
                    self.assertFalse(clicked.isError, clicked)
                    self.assertEqual(json.loads(clicked.content[0].text)['clicked'], cells['targets'][0]['id'])
                    for name, args in [('press_key', {'keys': ['enter']}),
                                       ('type_text', {'text': 'test'}),
                                       ('mouse', {'x': .5, 'y': .5}), ('release_all', {})]:
                        result = await client.call_tool(name, args)
                        self.assertFalse(result.isError, result)


class ApplicationLifecycleTests(unittest.TestCase):
    def test_startup_recovers_stale_marker_without_requiring_a_browser(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            gate = InputGate(Path(directory))
            gate.marker.touch()
            app = make_app('test-token-longer-than-twenty', backend, gate,
                           start_video=False, hosts=['testserver'])
            with TestClient(app):
                self.assertFalse(gate.manual)
                self.assertEqual(backend.events, [('keys', []), ('pointer', 0, 0, 0, 0)])
                with gate.automated():
                    pass

    def test_recovery_does_not_steal_an_active_browser_session(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = InputGate(Path(directory))
            active = Controller(FakeBackend(), gate)
            owner = object()
            active.claim(owner)
            try:
                with self.assertRaisesRegex(RuntimeError, 'busy'):
                    Controller(FakeBackend(), gate).recover_stale()
                self.assertTrue(gate.manual)
                self.assertIs(active.owner, owner)
            finally:
                active.disconnect(owner)

    def test_idle_control_socket_times_out_and_resumes_mcp(self):
        from starlette.websockets import WebSocketDisconnect
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            gate = InputGate(Path(directory))
            token = 'test-token-longer-than-twenty'
            app = make_app(token, backend, gate, start_video=False,
                           hosts=['testserver'], control_timeout=.1)
            with TestClient(app) as client:
                headers = {'Origin': 'http://testserver'}
                client.post('/login',data={'token':token},headers=headers)
                with client.websocket_connect('/ws/control',headers=headers) as socket:
                    socket.send_json({'type':'take'})
                    self.assertTrue(socket.receive_json()['controlling'])
                    with self.assertRaises(WebSocketDisconnect):
                        socket.receive_json()
                self.assertTrue(backend.closed_event.wait(2))
                self.assertFalse(gate.manual)
                with gate.automated():
                    pass

    def test_web_runs_on_jpeg_only_backend_and_cleans_up(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            app = make_app('test-token-longer-than-twenty', backend, InputGate(Path(directory)),
                           hosts=['testserver'])
            with TestClient(app) as client:
                self.assertTrue(backend.video.started)
                origin = {'Origin': 'http://testserver'}
                client.post('/login', data={'token': 'test-token-longer-than-twenty'}, headers=origin)
                status = client.get('/api/status').json()
                self.assertEqual(status['platform'], 'test-platform')
                self.assertTrue(status['input']['connected'])
                self.assertEqual(status['video']['transport'], 'jpeg')
                self.assertEqual(client.post('/api/rtc/offer', json={'type': 'offer', 'sdp': ''},
                                             headers=origin).status_code, 409)
                with client.websocket_connect('/ws/video', headers=origin) as ws:
                    ws.send_text('next')
                    with Image.open(io.BytesIO(ws.receive_bytes())) as image:
                        self.assertEqual(image.size, (640, 360))
            self.assertTrue(backend.video.stopped)

    def test_failed_return_ends_ownership_and_closes_input_session(self):
        backend = FakeBackend()
        with tempfile.TemporaryDirectory() as directory:
            gate = InputGate(Path(directory))
            controller = Controller(backend, gate)
            owner = object()
            controller.claim(owner)
            backend.fail_release = True
            with self.assertRaisesRegex(RuntimeError, '接管已结束'):
                controller.give_back(owner)
            self.assertFalse(gate.manual)
            self.assertIsNone(controller.owner)
            self.assertEqual(backend.opened, backend.closed)
            with gate.automated():
                self.assertFalse(gate.manual)

    def test_failed_claim_cleans_up_devices_and_lock(self):
        backend = FakeBackend()
        backend.fail_release = True
        with tempfile.TemporaryDirectory() as directory:
            gate = InputGate(Path(directory))
            controller = Controller(backend, gate)
            with self.assertRaisesRegex(OSError, 'disconnected'):
                controller.claim(object())
            self.assertIsNone(controller.owner)
            self.assertFalse(gate.manual)
            self.assertEqual(backend.opened, backend.closed)
            with gate.human():
                pass  # Failed claim must not leave its lock held.

    def test_second_controller_cannot_clear_live_owner_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            gate = InputGate(Path(directory))
            first = Controller(FakeBackend(), gate)
            second = Controller(FakeBackend(), InputGate(Path(directory)))
            owner = object()
            first.claim(owner)
            try:
                with self.assertRaisesRegex(RuntimeError, 'busy'):
                    second.give_back(object())
                self.assertTrue(gate.manual)
                self.assertIs(first.owner, owner)
            finally:
                first.give_back(owner)

    def test_marker_is_cleared_before_unlocking(self):
        class HandoffGate(InputGate):
            on_unlock = None

            @contextmanager
            def human(self):
                with super().human():
                    yield
                if self.on_unlock:
                    callback, self.on_unlock = self.on_unlock, None
                    callback()

        with tempfile.TemporaryDirectory() as directory:
            gate = HandoffGate(Path(directory))
            first = Controller(FakeBackend(), gate)
            second = Controller(FakeBackend(), gate)
            owner, other_owner = object(), object()
            first.claim(owner)
            gate.on_unlock = lambda: second.claim(other_owner)
            try:
                first.give_back(owner)
                self.assertTrue(gate.manual, 'A late unlink erased the next owner marker')
            finally:
                second.give_back(other_owner)
