"""Cross-process input ownership shared by MCP and web applications."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import os
import uuid

from wirehand.core.locking import exclusive_lock


@dataclass
class InputLease:
    revision: str | None = None


class InputGate:
    def __init__(self, runtime: Path):
        self.runtime = Path(runtime)

    @property
    def marker(self):
        return self.runtime / 'human-control'

    @property
    def manual(self):
        return self.marker.exists()

    @property
    def job_owner(self):
        try:
            return (self.runtime / 'automation-owner').read_text()
        except FileNotFoundError:
            return ''

    def check_automated(self):
        if self.manual:
            raise RuntimeError('Browser has manual control; wait for it to disconnect or return control')
        owner = self.job_owner
        if owner and owner != os.environ.get('KVM_AUTOMATION_ID'):
            raise RuntimeError('A script is running; input is reserved for that script')

    @contextmanager
    def automation_job(self):
        """Reserve automated input across script calls and repeat iterations."""
        if self.manual:
            raise RuntimeError('请先结束接管再执行脚本')
        with exclusive_lock(self.runtime, 'automation-job', timeout=0):
            with exclusive_lock(self.runtime, 'input', timeout=0):
                if self.manual:
                    raise RuntimeError('请先结束接管再执行脚本')
                token = uuid.uuid4().hex
                (self.runtime / 'automation-owner').write_text(token)
                self._advance()
            try:
                yield token
            finally:
                with exclusive_lock(self.runtime, 'input'):
                    (self.runtime / 'automation-owner').unlink(missing_ok=True)
                    self._advance()

    @property
    def revision(self):
        try:
            return (self.runtime / 'input-revision').read_text()
        except FileNotFoundError:
            return 'initial'

    def _advance(self):
        # Call only while holding input.lock. Atomic replacement lets observations
        # read the revision without acquiring the long-lived human-control lock.
        temporary = self.runtime / 'input-revision.tmp'
        revision = uuid.uuid4().hex
        temporary.write_text(revision)
        temporary.replace(self.runtime / 'input-revision')
        return revision

    @contextmanager
    def automated(self):
        self.check_automated()
        with exclusive_lock(self.runtime, 'input'):
            self.check_automated()
            lease = InputLease()
            try:
                yield lease
            finally:
                lease.revision = self._advance()

    @contextmanager
    def human(self):
        with exclusive_lock(self.runtime, 'input', timeout=0):
            if self.job_owner:
                raise RuntimeError('请先停止正在执行的脚本')
            self._advance()
            self.marker.touch(mode=0o600)
            try:
                yield
            finally:
                # The marker is a connection lease, not a persistent MCP pause.
                # Remove it while still locked so a new owner's marker survives.
                try:
                    self.marker.unlink(missing_ok=True)
                finally:
                    self._advance()
