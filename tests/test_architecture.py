"""Import boundaries and composition entry points are part of the architecture."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from kvm_mcp.bootstrap import load_backend

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/kvm_mcp'


class ArchitectureTests(unittest.TestCase):
    def test_application_and_platforms_only_share_contracts_and_infrastructure(self):
        layers = [
            (SOURCE/'app',SOURCE.parent,('kvm_rock5b_plus','kvm_mcp.bootstrap')),
            (ROOT/'packages/automation/src/kvm_automation',ROOT/'packages/automation/src',
             ('kvm_mcp','kvm_rock5b_plus','mcp','aiortc','starlette','uvicorn')),
            (ROOT/'packages/rock5b-plus/src/kvm_rock5b_plus',ROOT/'packages/rock5b-plus/src',
             ('kvm_mcp','mcp','aiortc','starlette','uvicorn')),
        ]
        for folder, source, forbidden in layers:
            paths = list(folder.rglob('*.py'))
            self.assertTrue(paths, f'Missing layer: {folder}')
            for path in paths:
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.ImportFrom):
                        package = '.'.join(path.relative_to(source).parts[:-1])
                        if node.level:
                            import importlib.util
                            name = importlib.util.resolve_name('.' * node.level + (node.module or ''), package)
                        else:
                            name = node.module or ''
                        names = [name]
                    elif isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    else:
                        continue
                    for name in names:
                        self.assertFalse(any(name == prefix or name.startswith(prefix + '.')
                                             for prefix in forbidden), f'{path}: {name}')

    def test_application_import_does_not_load_a_platform(self):
        code = '''import sys
from kvm_mcp.app.mcp import create_mcp
from kvm_mcp.app.web import make_app
assert not any(name.startswith('kvm_rock5b_plus') for name in sys.modules)
'''
        subprocess.run([sys.executable, '-c', code], check=True, capture_output=True, timeout=10)

    def test_platform_import_does_not_load_application_frameworks(self):
        code = '''import sys
from kvm_rock5b_plus.backend import Rock5BPlusBackend
from kvm_rock5b_plus.video import VideoHub
assert not any(name.split('.')[0] in ('mcp', 'starlette', 'aiortc') for name in sys.modules)
'''
        subprocess.run([sys.executable, '-c', code], check=True, capture_output=True, timeout=10)

    def test_standalone_library_does_not_import_applications_or_hardware(self):
        code = """import sys
from kvm_automation import Computer, Recorder, Recording
from kvm_automation.vision.service import VisionService
assert not any(name.split('.')[0] in ('kvm_mcp','kvm_rock5b_plus','mcp','aiortc','starlette','uvicorn') for name in sys.modules)
"""
        subprocess.run([sys.executable,'-c',code],check=True,capture_output=True,timeout=10)

    def test_backend_plugin_factory_and_no_implicit_platform_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = load_backend('tests.fakes:FakeBackend', Path(directory))
            self.assertEqual(backend.name, 'test-platform')
            with self.assertRaisesRegex(ValueError, 'module:factory'):
                load_backend('unknown-platform', Path(directory))
