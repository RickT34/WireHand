"""Lazily generate one isolated pdoc site per web application lifetime."""
import asyncio
from pathlib import Path
import sys
import tempfile


class Documentation:
    def __init__(self):
        self._temporary = None
        self._lock = asyncio.Lock()
        self._ready = False

    async def directory(self):
        async with self._lock:
            if self._temporary is None:
                self._temporary = tempfile.TemporaryDirectory(prefix='kvm-sdk-docs-')
            output = Path(self._temporary.name)
            if not self._ready:
                process = await asyncio.create_subprocess_exec(
                    sys.executable, '-m', 'wirehand.cli.docs', '--output', str(output),
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                try:
                    _, error = await asyncio.wait_for(process.communicate(), 45)
                except BaseException:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
                    raise
                if process.returncode:
                    raise RuntimeError('SDK 文档生成失败：' + error.decode(errors='replace')[-1000:])
                self._ready = True
            return output

    def close(self):
        if self._temporary:
            self._temporary.cleanup()
