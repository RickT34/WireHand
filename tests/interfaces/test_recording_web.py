import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from starlette.testclient import TestClient
from wirehand.automation import Recording,Computer
from wirehand.core.ownership import InputGate
from wirehand.interfaces.web.app import make_app
from tests.automation.helpers import ScreenBackend,screen


class WebRecordingTests(unittest.TestCase):
    token='test-secret-is-at-least-twenty-characters'
    origin={'Origin':'http://testserver'}

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.backend=ScreenBackend()
        self.app=make_app(self.token,self.backend,InputGate(self.root/'runtime'),
                          hosts=['testserver'],start_video=False,recordings_dir=self.root/'recordings')
        self.client=self.enterContext(TestClient(self.app))
        self.client.post('/login',data={'token':self.token},headers=self.origin)

    def test_browser_recording_download_and_moved_target_replay(self):
        with self.client.websocket_connect('/ws/control',headers=self.origin) as ws:
            ws.send_json({'type':'take'});self.assertTrue(ws.receive_json()['controlling'])
            ws.send_json({'type':'record_start'});self.assertTrue(ws.receive_json()['recording']['active'])
            ws.send_json({'type':'pointer','x':100/479,'y':90/319,'buttons':1});self.assertTrue(ws.receive_json()['ok'])
            ws.send_json({'type':'pointer','x':100/479,'y':90/319,'buttons':0});self.assertTrue(ws.receive_json()['ok'])
            ws.send_json({'type':'record_stop'})
            result=ws.receive_json()
            self.assertTrue(result['ok'],result)
            self.assertFalse(result['recording']['active'])
            record=result['recording']['last']
            self.assertEqual(record['status'],'complete')
        response=self.client.get(f'/api/recordings/{record["id"]}/download')
        self.assertEqual(response.status_code,200,response.text[:100] if response.status_code!=200 else '')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertIn(f'{record["id"]}/replay.py',archive.namelist())
            data=json.loads(archive.read(f'{record["id"]}/recording.json'))
            self.assertEqual(data['steps'][0]['action'],'click')
        moved=ScreenBackend(screen((240,170)))
        Recording.load(self.root/'recordings'/record['id']).replay(
            Computer(moved,self.root/'replay',timeout=.5,poll_interval=.001))
        self.assertAlmostEqual(moved.events[0][1],290/479,delta=.005)

    def test_viewer_cannot_start_recording(self):
        with self.client.websocket_connect('/ws/control',headers=self.origin) as ws:
            ws.send_json({'type':'record_start'})
            result=ws.receive_json()
            self.assertFalse(result['ok'])
            self.assertFalse(result['recording']['active'])
        self.assertFalse((self.root/'recordings').exists())

    def test_disconnect_saves_an_interrupted_recording_and_restores_control(self):
        with self.client.websocket_connect('/ws/control',headers=self.origin) as ws:
            ws.send_json({'type':'take'});ws.receive_json()
            ws.send_json({'type':'record_start'});ws.receive_json()
            ws.send_json({'type':'pointer','x':100/479,'y':90/319,'buttons':1});ws.receive_json()
            ws.send_json({'type':'pointer','x':100/479,'y':90/319,'buttons':0});ws.receive_json()
        self.assertTrue(self.backend.closed_event.wait(2))
        record=self.app.state.services.controller.last_recording
        self.assertEqual(record['status'],'interrupted')
        with self.assertRaisesRegex(ValueError,'incomplete'):
            Recording.load(self.root/'recordings'/record['id'])
        self.assertFalse(self.app.state.services.controller.gate.manual)

    def test_download_is_authenticated_and_cannot_escape_recording_root(self):
        self.client.cookies.clear()
        response=self.client.get('/api/recordings/'+'a'*32+'/download')
        self.assertEqual(response.status_code,401)
        self.client.post('/login',data={'token':self.token},headers=self.origin)
        self.assertEqual(self.client.get('/api/recordings/invalid/download').status_code,404)
