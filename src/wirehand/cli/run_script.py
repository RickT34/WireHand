"""Child-process entry point for one iteration of an editable script bundle."""
from pathlib import Path
import os
import runpy
import signal
import sys


def main():
    if sys.platform == 'linux':
        import ctypes
        # Do not leave input automation running after its supervisor crashes.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'Cannot watch the script supervisor')
        if os.getppid() != int(os.environ['KVM_SUPERVISOR_PID']):
            return
    script = Path(sys.argv[1]).resolve()
    # Let context managers release input on an ordinary stop. The supervisor
    # escalates to SIGKILL if user code ignores termination or stays blocked.
    def stop(signum, frame):
        raise KeyboardInterrupt('Script stopped')
    signal.signal(signal.SIGTERM, stop)
    sys.path.insert(0, str(script.parent))
    try:
        runpy.run_path(str(script), run_name='__main__')
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
