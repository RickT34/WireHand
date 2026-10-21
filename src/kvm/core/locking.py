"""Shared local-process locks. No device knowledge or ownership policy."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import time


@contextmanager
def exclusive_lock(runtime: Path, name: str, timeout: float = 2):
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (runtime / f'{name}.lock').open('a') as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('KVM busy in another session')
                time.sleep(0.02)
        yield
