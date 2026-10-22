"""Browser script management and execution through the authenticated HTTP API."""
import asyncio
import io
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote
import zipfile

from starlette.testclient import TestClient

from wirehand.core.ownership import InputGate
from wirehand.interfaces.web.app import make_app
from tests.automation.test_scripts import bundle
from tests.fakes import FakeBackend


class ScriptWebTests(unittest.TestCase):
    token = 'test-script-library-password-long-enough'
    origin = {'Origin': 'http://testserver'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = FakeBackend()
        self.app = make_app(self.token, self.backend, InputGate(self.root/'runtime'),
                            start_video=False, hosts=['testserver'], recordings_dir=self.root/'scripts')
        self.client = self.enterContext(TestClient(self.app))
        self.client.post('/login', data={'token':self.token}, headers=self.origin)

    def upload(self, script="print('edited')", filename='操作.zip'):
        response = self.client.post('/api/scripts', params={'filename':filename},
                                    content=bundle(script), headers=self.origin)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def wait(self):
        async def wait():
            await asyncio.wait_for(self.app.state.services.runner.task, 6)
        self.client.portal.call(wait)
        return self.client.get('/api/scripts/run').json()

    def test_list_rename_download_and_replace_by_archive_filename(self):
        item = self.upload()
        self.assertEqual(self.client.get('/api/scripts').json()['scripts'][0]['name'], '操作')
        renamed = self.client.patch('/api/scripts/'+item['id'], json={'name':'调试版本'}, headers=self.origin)
        self.assertEqual(renamed.status_code, 200)
        download = self.client.get('/api/scripts/'+item['id']+'/download')
        self.assertIn(quote('调试版本.zip'), download.headers['content-disposition'])
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            self.assertEqual(archive.read(item['id']+'/replay.py'), b"print('edited')")
        conflict = self.client.post('/api/scripts', params={'filename':'调试版本.zip'},
                                   content=bundle("print('replacement')"), headers=self.origin)
        self.assertEqual(conflict.status_code, 409)
        existing = conflict.json()['existing']
        replaced = self.client.post('/api/scripts', params={'filename':'调试版本.zip','replace':existing['id'],'revision':existing['revision']},
                                    content=bundle("print('replacement')"), headers=self.origin)
        self.assertEqual(replaced.status_code, 201)
        run = self.client.post('/api/scripts/'+item['id']+'/run', json={'repeat':2}, headers=self.origin)
        self.assertEqual(run.status_code, 202, run.text)
        status = self.wait()
        self.assertEqual(status['completed'], 2)
        self.assertEqual(status['log'].count('replacement'), 2)

    def test_takeover_stops_infinite_job_and_blocks_all_further_runs(self):
        item = self.upload("import time\nprint('running',flush=True)\ntime.sleep(60)")
        self.client.post('/api/scripts/'+item['id']+'/run', json={'repeat':0}, headers=self.origin)
        with self.client.websocket_connect('/ws/control', headers=self.origin) as ws:
            ws.send_json({'type':'take'})
            response = ws.receive_json()
            self.assertTrue(response['controlling'], response)
            state = self.client.get('/api/scripts/run').json()
            self.assertFalse(state['active'])
            self.assertEqual(state['status'], 'stopped')
            blocked = self.client.post('/api/scripts/'+item['id']+'/run', json={'repeat':1}, headers=self.origin)
            self.assertEqual(blocked.status_code, 409)
        self.assertFalse(self.app.state.services.controller.gate.manual)

    def test_active_script_cannot_be_replaced_deleted_or_started_twice(self):
        item = self.upload('import time\ntime.sleep(60)')
        self.client.post('/api/scripts/'+item['id']+'/run', json={'repeat':0}, headers=self.origin)
        replacement = self.client.post('/api/scripts', params={'filename':'操作.zip','replace':item['id'],'revision':item['revision']},
                                       content=bundle(), headers=self.origin)
        self.assertEqual(replacement.status_code, 409)
        self.assertEqual(self.client.delete('/api/scripts/'+item['id'], headers=self.origin).status_code, 409)
        self.assertEqual(self.client.post('/api/scripts/'+item['id']+'/run', json={}, headers=self.origin).status_code, 409)
        stopped = self.client.post('/api/scripts/stop', headers=self.origin)
        self.assertEqual(stopped.status_code, 200)
        self.assertFalse(stopped.json()['active'])

    def test_recording_is_named_and_persisted_without_downloading(self):
        with self.client.websocket_connect('/ws/control', headers=self.origin) as ws:
            ws.send_json({'type':'take'}); ws.receive_json()
            ws.send_json({'type':'record_start','name':'我的录制'}); self.assertTrue(ws.receive_json()['ok'])
            ws.send_json({'type':'record_stop'}); self.assertTrue(ws.receive_json()['ok'])
        listed = self.client.get('/api/scripts').json()['scripts']
        self.assertEqual(listed[0]['name'], '我的录制')
        self.assertTrue(listed[0]['runnable'])
        self.assertTrue((self.root/'scripts'/listed[0]['id']/'replay.py').exists())

    def test_mutations_require_same_origin_and_reading_requires_login(self):
        item = self.upload()
        for method, path in [('post','/api/scripts'),('post','/api/scripts/stop'),
                             ('post','/api/scripts/'+item['id']+'/run'),('patch','/api/scripts/'+item['id']),
                             ('delete','/api/scripts/'+item['id'])]:
            with self.subTest(method=method,path=path):
                response = getattr(self.client,method)(path,headers={'Origin':'http://evil.example'})
                self.assertEqual(response.status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/scripts').status_code, 401)
        self.assertEqual(self.client.get('/api/scripts/run').status_code, 401)
        self.assertEqual(self.client.get('/api/scripts/'+item['id']+'/download').status_code, 401)

    def test_invalid_repeat_and_json_never_start_a_process(self):
        item = self.upload()
        for repeat in [-1, True, 1.5, 'forever', 100001]:
            with self.subTest(repeat=repeat):
                response = self.client.post('/api/scripts/'+item['id']+'/run', json={'repeat':repeat}, headers=self.origin)
                self.assertEqual(response.status_code, 400)
        self.assertFalse(self.client.get('/api/scripts/run').json()['active'])

    def test_docs_are_generated_from_live_sdk_signatures_and_require_auth(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/docs/wirehand/automation/computer.html',follow_redirects=False).status_code, 307)
        self.client.post('/login', data={'token':self.token}, headers=self.origin)
        root = self.client.get('/docs/')
        self.assertTrue(str(root.url).endswith('/docs/wirehand/factory.html'))
        self.assertEqual(self.client.get('/docs/index.html').status_code, 200)
        response = self.client.get('/docs/wirehand/automation/computer.html')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Computer.click', response.text)
        self.assertIn('hold_ms', response.text)
        self.assertIn('视觉目标', response.text)
        self.assertEqual(self.client.get('/docs/%2e%2e%2f%2e%2e%2fetc/passwd').status_code, 404)

    def test_service_shutdown_stops_an_active_script_and_releases_input(self):
        gate = InputGate(self.root/'shutdown-runtime')
        app = make_app(self.token, self.backend, gate, start_video=False,
                       hosts=['testserver'], recordings_dir=self.root/'shutdown-scripts')
        with TestClient(app) as client:
            client.post('/login', data={'token':self.token}, headers=self.origin)
            uploaded = client.post('/api/scripts', params={'filename':'shutdown.zip'}, headers=self.origin,
                                   content=bundle("import time\nprint('ready',flush=True)\ntime.sleep(60)")).json()
            response = client.post('/api/scripts/'+uploaded['id']+'/run', json={'repeat':0}, headers=self.origin)
            self.assertEqual(response.status_code, 202)
            async def ready():
                async with asyncio.timeout(4):
                    while 'ready' not in app.state.services.runner.state['log']:
                        await asyncio.sleep(.01)
            client.portal.call(ready)
        self.assertFalse(app.state.services.runner.active)
        self.assertEqual(app.state.services.runner.state['status'], 'stopped')
        self.assertEqual(gate.job_owner, '')
        self.assertEqual(self.backend.opened, self.backend.closed)
