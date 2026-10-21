from pathlib import Path
import tempfile
import unittest

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kvm_mcp.app.control import Controller
from kvm_automation.ownership import InputGate
from kvm_mcp.app.web import make_app
from tests.fakes import FakeBackend


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = FakeBackend()
        self.reports = self.backend.events
        self.gate = InputGate(Path(self.temp.name))
        self.control = Controller(self.backend, self.gate)
        self.owner = object()
        self.addCleanup(lambda: self.control.disconnect(self.owner))

    def test_viewer_cannot_send_input(self):
        with self.assertRaisesRegex(RuntimeError, '接管'):
            self.control.action(self.owner, {'type': 'key', 'code':'KeyA', 'down':True})
        self.assertEqual(self.reports, [])

    def test_second_browser_cannot_steal_control(self):
        self.control.claim(self.owner)
        with self.assertRaisesRegex(RuntimeError, '另一个'):
            self.control.claim(object())
        self.assertIs(self.control.owner, self.owner)

    def test_disconnect_releases_pressed_key_and_resumes_mcp(self):
        self.control.claim(self.owner)
        self.control.action(self.owner, {'type':'key', 'code':'KeyA', 'down':True})
        self.control.disconnect(self.owner)
        self.assertEqual(self.reports[-2:], [('keys', []), ('pointer', 0, 0, 0, 0)])
        with self.gate.automated():
            self.assertFalse(self.control.marker.exists())

    def test_explicit_return_allows_mcp_again(self):
        self.control.claim(self.owner)
        self.control.give_back(self.owner)
        with self.gate.automated():
            self.assertFalse(self.control.marker.exists())

    def test_return_after_restart_resets_host_before_unpausing(self):
        self.control.marker.touch()
        self.control.give_back(self.owner)
        self.assertIn(('keys', []), self.reports)
        self.assertIn(('pointer', 0, 0, 0, 0), self.reports)
        self.assertFalse(self.control.marker.exists())

    def test_held_modifier_and_drag_reports(self):
        self.control.claim(self.owner)
        self.control.action(self.owner, {'type':'key', 'code':'ControlLeft', 'down':True})
        self.control.action(self.owner, {'type':'key', 'code':'KeyA', 'down':True})
        self.control.action(self.owner, {'type':'pointer', 'x':1, 'y':1, 'buttons':1})
        self.assertEqual(self.reports[-2:], [
            ('keys', ['ctrl', 'a']),
            ('pointer', 1, 1, 1, 0)])


class WebTests(unittest.TestCase):
    token = 'test-secret-is-at-least-twenty-characters'
    origin = {'origin':'http://testserver'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = FakeBackend()
        self.reports = self.backend.events
        self.gate = InputGate(Path(self.temp.name))
        app = make_app(self.token, self.backend, self.gate,
                       start_video=False, hosts=['testserver'])
        self.control = app.state.controller
        self.client = self.enterContext(TestClient(app))

    def login(self):
        response = self.client.post('/login', data={'token': self.token}, headers=self.origin)
        self.assertEqual(response.status_code, 200)

    def test_anonymous_cannot_read_status(self):
        self.assertEqual(self.client.get('/api/status').status_code, 401)

    def test_browser_diagnostics_are_authenticated_and_allowlisted(self):
        self.assertEqual(self.client.get('/api/rtc/diagnostics').status_code,401)
        self.login()
        response=self.client.post('/api/rtc/report',headers=self.origin,json={
            'reason':'decode timeout','diagnostic':{'framesDecoded':0,'connection':'connected','sdp':'secret'}})
        self.assertEqual(response.status_code,200)
        record=self.client.get('/api/rtc/diagnostics').json()[0]
        self.assertEqual(record['diagnostic'],{'framesDecoded':0,'connection':'connected'})

    def test_anonymous_cannot_allocate_webrtc_peer(self):
        response = self.client.post('/api/rtc/offer',json={'type':'offer','sdp':''},headers=self.origin)
        self.assertEqual(response.status_code,401)

    def test_cross_origin_cannot_allocate_webrtc_peer(self):
        self.login()
        response = self.client.post('/api/rtc/offer',json={'type':'offer','sdp':''},
                                    headers={'Origin':'http://other.example'})
        self.assertEqual(response.status_code,403)

    def test_wrong_password_does_not_create_session(self):
        response = self.client.post('/login', data={'token':'wrong'}, headers=self.origin)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('kvm_session', self.client.cookies)

    def test_untrusted_host_rejected(self):
        self.assertEqual(self.client.get('/', headers={'host':'attacker.example'}).status_code, 400)

    def test_websocket_cross_origin_rejected_even_when_logged_in(self):
        self.login()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/ws/control', headers={'origin':'http://other.example'}):
                self.fail('Cross-origin WebSocket accepted')

    def test_view_only_socket_rejects_input_server_side(self):
        self.login()
        with self.client.websocket_connect('/ws/control', headers=self.origin) as ws:
            ws.send_json({'type':'key','code':'KeyA','down':True})
            reply = ws.receive_json()
        self.assertFalse(reply['ok'])
        self.assertFalse(reply['controlling'])
        self.assertEqual(self.reports, [])

    def test_socket_disconnect_releases_hid_and_resumes_mcp(self):
        self.login()
        with self.client.websocket_connect('/ws/control', headers=self.origin) as ws:
            ws.send_json({'type':'take'})
            self.assertTrue(ws.receive_json()['controlling'])
            ws.send_json({'type':'key','code':'KeyA','down':True})
            self.assertTrue(ws.receive_json()['ok'])
            ws.close()
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()
        self.assertTrue(self.backend.closed_event.wait(2), 'Disconnected input session was not closed')
        self.assertFalse(self.control.marker.exists())
        self.assertEqual(self.reports[-2:], [('keys', []), ('pointer', 0, 0, 0, 0)])


if __name__ == '__main__':
    unittest.main()
