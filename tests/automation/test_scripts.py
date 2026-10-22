"""User-edited bundles, replacement safety, and real subprocess lifecycle."""
import asyncio
import io
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile

from wirehand.automation.scripts.library import NameConflict, ScriptLibrary
from wirehand.automation.scripts.runner import ScriptRunner
from wirehand.core.ownership import InputGate
from tests.fakes import FakeBackend


def bundle(script="print('original')\n", files=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('flow/replay.py', script)
        for name, data in (files or {}).items():
            archive.writestr(name, data)
    return output.getvalue()


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.library = ScriptLibrary(self.temp.name)

    def test_names_survive_restart_and_download_preserves_edited_python(self):
        item = self.library.import_zip(bundle("print('edited')\n"), '每日操作.zip')
        restored = ScriptLibrary(self.temp.name)
        self.assertEqual(restored.list()[0]['name'], '每日操作')
        name, raw = restored.export(item['id'])
        self.assertEqual(name, '每日操作')
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertEqual(archive.read(f'{item["id"]}/replay.py'), b"print('edited')\n")

    def test_replacement_requires_confirmation_of_current_revision(self):
        original = self.library.import_zip(bundle(), 'same.zip')
        with self.assertRaises(NameConflict):
            self.library.import_zip(bundle("print('new')"), 'same.zip')
        self.assertIn('original', (self.library.path(original['id'])/'replay.py').read_text())
        replacement = self.library.import_zip(bundle("print('new')"), 'same.zip',
                                             replace_id=original['id'], revision=original['revision'])
        self.assertEqual(replacement['id'], original['id'])
        self.assertNotEqual(replacement['revision'], original['revision'])
        with self.assertRaises(NameConflict):
            self.library.import_zip(bundle(), 'same.zip', replace_id=original['id'], revision=original['revision'])
        self.assertIn('new', (self.library.path(original['id'])/'replay.py').read_text())

    def test_invalid_replacement_leaves_original_intact(self):
        original = self.library.import_zip(bundle(), 'same.zip')
        with self.assertRaises(ValueError):
            self.library.import_zip(bundle('def broken('), 'same.zip',
                                    replace_id=original['id'], revision=original['revision'])
        self.assertEqual(self.library.info(original['id'])['revision'], original['revision'])

    def test_zip_traversal_symlinks_duplicates_and_unrelated_files_are_rejected(self):
        link = zipfile.ZipInfo('flow/link.py');link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archives = [bundle(files={'../escape': 'oops'}), bundle(files={'/absolute': 'oops'}),
                    bundle(files={'flow/../../escape': 'oops'}),bundle(files={'flow\\escape':'oops'}),
                    bundle(files={link:'replay.py'}),bundle(files={'flow/replay.py':'duplicate'}),
                    bundle(files={'other/file.txt':'outside root'})]
        for raw in archives:
            with self.subTest(raw_length=len(raw)), self.assertRaises(ValueError):
                self.library.import_zip(raw, 'bad.zip')
        self.assertEqual(self.library.list(), [])

    def test_names_cannot_escape_and_case_variants_cannot_duplicate(self):
        original = self.library.import_zip(bundle(), 'Demo.zip')
        with self.assertRaises(NameConflict):
            self.library.import_zip(bundle(), 'demo.zip')
        with self.assertRaises(ValueError):
            self.library.rename(original['id'], '../outside')
        self.library.rename(original['id'], '新名字')
        self.assertEqual(self.library.export(original['id'])[0], '新名字')


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = ScriptLibrary(self.root/'scripts')
        self.backend = FakeBackend()
        self.gate = InputGate(self.root/'runtime')
        self.runner = ScriptRunner(self.backend, self.gate, 'unused')
        self.addAsyncCleanup(self.runner.stop)

    async def wait_for_log(self, text):
        async with asyncio.timeout(5):
            while text not in self.runner.state['log']:
                await asyncio.sleep(.01)

    def start(self, script, repeat=1, files=None):
        item = self.library.import_zip(bundle(script, files), 'test.zip')
        self.runner.start(self.library.path(item['id']), item, repeat)
        return item

    async def test_runs_edited_python_and_local_imports_for_exact_repeat_count(self):
        item = self.start("from helper import value\nfrom pathlib import Path\np=Path('count.txt')\np.write_text(p.read_text()+value if p.exists() else value)\nprint(value)",
                          repeat=3, files={'flow/helper.py':"value='edited'"})
        await asyncio.wait_for(self.runner.task, 8)
        self.assertEqual(self.runner.state['status'], 'completed', self.runner.state)
        self.assertEqual(self.runner.state['completed'], 3)
        self.assertEqual((self.library.path(item['id'])/'count.txt').read_text(), 'editededitededited')
        self.assertEqual(self.gate.job_owner, '')
        self.assertEqual(self.backend.opened, self.backend.closed)

    async def test_script_error_stops_further_repetitions_and_preserves_traceback(self):
        self.start("raise ValueError('script failed deliberately')", repeat=3)
        await asyncio.wait_for(self.runner.task, 5)
        self.assertEqual(self.runner.state['status'], 'failed')
        self.assertEqual(self.runner.state['completed'], 0)
        self.assertIn('script failed deliberately', self.runner.state['log'])
        with self.gate.automated():
            pass

    async def test_infinite_run_can_be_stopped_and_releases_input_reservation(self):
        self.start("import time\nprint('ready', flush=True)\ntime.sleep(60)", repeat=0)
        await self.wait_for_log('ready')
        with self.assertRaisesRegex(RuntimeError, 'reserved'):
            with self.gate.automated():
                pass
        await asyncio.wait_for(self.runner.stop(), 4)
        self.assertFalse(self.runner.active)
        self.assertEqual(self.runner.state['status'], 'stopped')
        self.assertEqual(self.backend.events[-2:], [('keys', []), ('pointer', 0, 0, 0, 0)])
        with self.gate.human():
            self.assertTrue(self.gate.manual)

    async def test_stop_kills_a_script_that_ignores_sigterm(self):
        self.start("import signal,time\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nprint('ready',flush=True)\ntime.sleep(60)")
        await self.wait_for_log('ready')
        await asyncio.wait_for(self.runner.stop(), 4)
        self.assertEqual(self.runner.state['status'], 'stopped')
        self.assertEqual(self.gate.job_owner, '')

    async def test_manual_control_prevents_execution_before_any_python_runs(self):
        item = self.library.import_zip(bundle("raise AssertionError('must not run')"), 'test.zip')
        with self.gate.human(), self.assertRaisesRegex(RuntimeError, '接管'):
            self.runner.start(self.library.path(item['id']), item)
        self.assertFalse(self.runner.active)
        self.assertEqual(self.backend.events, [])

    async def test_new_sdk_sessions_in_child_share_the_reserved_input_token(self):
        helper = '''from contextlib import contextmanager
from pathlib import Path
class Backend:
    def __init__(self, runtime): pass
    @contextmanager
    def open_input(self):
        class Input:
            def press_key(self, keys, hold_ms):
                with Path('input.txt').open('a') as output: output.write(','.join(keys)+'\\n')
        yield Input()
'''
        self.runner.backend_spec = 'helper:Backend'
        item = self.start("from wirehand import connect\nwith connect() as computer:\n    computer.press('esc')\n    computer.press('enter')", repeat=2,
                          files={'flow/helper.py':helper})
        await asyncio.wait_for(self.runner.task, 8)
        self.assertEqual(self.runner.state['status'], 'completed', self.runner.state)
        self.assertEqual((self.library.path(item['id'])/'input.txt').read_text(), 'esc\nenter\nesc\nenter\n')

    async def test_second_runner_cannot_acquire_a_live_job_but_restart_recovers_stale_owner(self):
        item = self.start("import time\nprint('ready',flush=True)\ntime.sleep(60)")
        await self.wait_for_log('ready')
        other = ScriptRunner(self.backend, self.gate, 'unused')
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            other.start(self.library.path(item['id']), self.library.info(item['id']))
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            other.recover_stale()
        await self.runner.stop()
        (self.gate.runtime/'automation-owner').write_text('abandoned')
        other.recover_stale()
        self.assertEqual(self.gate.job_owner, '')
        with self.gate.human():
            pass

    async def test_completed_script_does_not_leave_a_descendant_holding_its_output_pipe(self):
        self.start("import subprocess,sys\nsubprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\nprint('main finished')")
        await asyncio.wait_for(self.runner.task, 4)
        self.assertEqual(self.runner.state['status'], 'completed', self.runner.state)
        self.assertIn('main finished', self.runner.state['log'])
        self.assertEqual(self.gate.job_owner, '')
