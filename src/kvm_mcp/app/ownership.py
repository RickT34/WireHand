"""Cross-process input ownership shared by MCP and web applications."""
from contextlib import contextmanager
from pathlib import Path
import uuid

from kvm_mcp.runtime import exclusive_lock


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
    def revision(self):
        try:
            return (self.runtime / 'input-revision').read_text()
        except FileNotFoundError:
            return 'initial'

    def _advance(self):
        # Call only while holding input.lock. Atomic replacement lets observations
        # read the revision without acquiring the long-lived human-control lock.
        temporary = self.runtime / 'input-revision.tmp'
        temporary.write_text(uuid.uuid4().hex)
        temporary.replace(self.runtime / 'input-revision')

    @contextmanager
    def automated(self):
        if self.manual:
            raise RuntimeError('Browser has manual control; wait for it to disconnect or return control')
        with exclusive_lock(self.runtime, 'input'):
            if self.manual:
                raise RuntimeError('Browser has manual control; wait for it to disconnect or return control')
            try:
                yield
            finally:
                self._advance()

    @contextmanager
    def human(self):
        with exclusive_lock(self.runtime, 'input', timeout=0):
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
