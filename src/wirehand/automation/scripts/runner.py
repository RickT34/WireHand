"""Run editable Python bundles in child processes with bounded logs and cancellation."""
import asyncio
from contextlib import suppress
import os
from pathlib import Path
import signal
import sys
import time

from wirehand.core.locking import exclusive_lock


class ScriptRunner:
    def __init__(self, backend, gate, backend_spec):
        self.backend, self.gate, self.backend_spec = backend, gate, backend_spec
        self.task = None
        self.process = None
        self.stopping = False
        self.state = {'status': 'idle', 'completed': 0, 'log': ''}

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def status(self):
        return {**self.state, 'active': self.active}

    def recover_stale(self):
        """Recover a crashed service's reservation without stealing a live job."""
        if not self.gate.job_owner:
            return
        with exclusive_lock(self.gate.runtime, 'automation-job', timeout=0):
            with exclusive_lock(self.gate.runtime, 'input', timeout=0):
                try:
                    with self.backend.open_input() as session:
                        session.release_all()
                finally:
                    (self.gate.runtime / 'automation-owner').unlink(missing_ok=True)
                    self.gate._advance()

    def start(self, directory, info, repeat=1):
        if self.active:
            raise RuntimeError('已有脚本正在执行')
        if type(repeat) is not int or not 0 <= repeat <= 100000:
            raise ValueError('重复次数须为 1–100000；0 表示无限重复')
        if not info['runnable']:
            raise ValueError('录制尚未完成，不能执行')
        reservation = self.gate.automation_job()
        token = reservation.__enter__()
        self.stopping = False
        self.state = {'status': 'running', 'script_id': info['id'], 'name': info['name'],
                      'repeat': repeat, 'completed': 0, 'iteration': 1, 'log': '',
                      'started_at': time.time(), 'error': ''}
        self.task = asyncio.create_task(self._run(Path(directory).resolve(), repeat, token, reservation))
        return self.status()

    def _log(self, text):
        self.state['log'] = (self.state['log'] + text)[-32768:]

    @staticmethod
    def _signal(process, value):
        with suppress(ProcessLookupError):
            os.killpg(process.pid, value)

    async def _read_output(self, process):
        while chunk := await process.stdout.read(4096):
            self._log(chunk.decode('utf-8', errors='replace'))

    async def _run(self, directory, repeat, token, reservation):
        try:
            while not self.stopping and (repeat == 0 or self.state['completed'] < repeat):
                self.state['iteration'] = self.state['completed'] + 1
                env = {**os.environ, 'KVM_BACKEND': self.backend_spec,
                       'KVM_RUNTIME': str(self.gate.runtime.resolve()), 'KVM_AUTOMATION_ID': token,
                       'KVM_SUPERVISOR_PID': str(os.getpid()),
                       'PYTHONUNBUFFERED': '1'}
                # Access password is only needed by the web service.
                env.pop('KVM_TOKEN', None)
                process = await asyncio.create_subprocess_exec(
                    sys.executable, '-m', 'wirehand.cli.run_script', str(directory / 'replay.py'),
                    cwd=directory, env=env, start_new_session=True,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                self.process = process
                reader = asyncio.create_task(self._read_output(process))
                try:
                    if self.stopping:
                        self._signal(process, signal.SIGTERM)
                    # Process.wait() can wait for inherited stdout pipes held
                    # by descendants after the main script has already exited.
                    while process.returncode is None:
                        await asyncio.sleep(.025)
                    code = process.returncode
                finally:
                    # Descendants must not outlive a completed/stopped iteration.
                    self._signal(process, signal.SIGKILL)
                    await process.wait()
                    await reader
                    self.process = None
                if self.stopping:
                    break
                if code:
                    raise RuntimeError(f'脚本退出码 {code}，请查看运行日志')
                self.state['completed'] += 1
                await asyncio.sleep(.1)  # Empty infinite scripts must not spin.
            self.state['status'] = 'stopped' if self.stopping else 'completed'
        except Exception as exc:
            self.state.update(status='failed', error=str(exc))
            self._log(f'\n{exc}\n')
        finally:
            try:
                await asyncio.to_thread(self._release_input)
            except Exception as exc:
                self.state.update(status='failed', error=f'释放输入失败：{exc}')
                self._log(f'\n释放输入失败：{exc}\n')
            finally:
                reservation.__exit__(None, None, None)
                self.state['finished_at'] = time.time()

    def _release_input(self):
        with exclusive_lock(self.gate.runtime, 'input'), self.backend.open_input() as session:
            session.release_all()

    async def stop(self):
        if not self.active:
            return self.status()
        self.stopping = True
        self.state['status'] = 'stopping'
        if self.process:
            self._signal(self.process, signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(self.task), 1.5)
        except asyncio.TimeoutError:
            if self.process:
                self._signal(self.process, signal.SIGKILL)
            await asyncio.shield(self.task)
        return self.status()
