"""Enforce dependency direction and lazy composition independently of hardware."""
import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from kvm import connect
from kvm.platforms.registry import load_backend
from tests.fakes import FakeBackend

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/kvm'
FRAMEWORKS = ('mcp', 'aiortc', 'starlette', 'uvicorn')


class ArchitectureTests(unittest.TestCase):
    def test_layer_dependency_direction(self):
        layers = {
            'core': ('core',),
            'vision': ('vision',),
            'automation': ('automation', 'core', 'vision'),
            'platforms': ('platforms', 'core'),
            'interfaces/mcp': ('interfaces.mcp', 'core', 'automation', 'vision'),
            'interfaces/web': ('interfaces.web', 'core', 'automation', 'vision'),
        }
        for folder, allowed in layers.items():
            paths = list((SOURCE / folder).rglob('*.py'))
            self.assertTrue(paths, f'Missing layer: {folder}')
            for path in paths:
                package = '.'.join(path.relative_to(SOURCE.parent).parts[:-1])
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.ImportFrom):
                        name = importlib.util.resolve_name('.' * node.level + (node.module or ''), package) if node.level else node.module or ''
                        names = [name] if name != 'kvm' else [f'kvm.{a.name}' for a in node.names]
                    elif isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    else:
                        continue
                    for name in names:
                        if name == 'kvm' or name.startswith('kvm.'):
                            self.assertTrue(any(name == f'kvm.{p}' or name.startswith(f'kvm.{p}.') for p in allowed), f'{path}: forbidden dependency {name}')
                        elif not folder.startswith('interfaces/'):
                            self.assertNotIn(name.split('.')[0], FRAMEWORKS, f'{path}: {name}')

    def assert_isolated_import(self, code):
        subprocess.run([sys.executable, '-c', code], check=True, capture_output=True, timeout=10)

    def test_interface_import_does_not_load_platforms(self):
        self.assert_isolated_import('''import sys
from kvm.interfaces.mcp.server import create_mcp
from kvm.interfaces.web.app import make_app
assert not any(n.startswith('kvm.platforms') for n in sys.modules)
''')

    def test_platform_import_does_not_load_automation_or_interfaces(self):
        self.assert_isolated_import('''import sys
from kvm.platforms.rock5b_plus.backend import Rock5BPlusBackend
from kvm.platforms.rock5b_plus.video import VideoHub
assert not any(n.startswith(('kvm.automation', 'kvm.vision', 'kvm.interfaces')) for n in sys.modules)
assert not any(n.split('.')[0] in ('mcp', 'starlette', 'aiortc') for n in sys.modules)
''')

    def test_sdk_import_does_not_load_platforms_or_interfaces(self):
        self.assert_isolated_import('''import sys
from kvm import connect
from kvm.automation import Computer, Recorder, Recording
from kvm.automation.inspection import Inspector
assert not any(n.startswith(('kvm.platforms', 'kvm.interfaces')) for n in sys.modules)
assert not any(n.split('.')[0] in ('mcp', 'aiortc', 'starlette', 'uvicorn') for n in sys.modules)
''')

    def test_visual_algorithms_do_not_load_device_or_automation_code(self):
        self.assert_isolated_import('''import sys
from kvm.vision import VisualTarget
from kvm.vision.detection import ContourDetector
from kvm.vision.rendering import partition
assert not any(n.startswith(('kvm.core', 'kvm.automation', 'kvm.platforms', 'kvm.interfaces')) for n in sys.modules)
''')

    def test_explicit_backend_factory_and_unknown_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = load_backend('tests.fakes:FakeBackend', Path(directory))
            self.assertEqual(backend.name, 'test-platform')
            with self.assertRaisesRegex(ValueError, 'module:factory'):
                load_backend('unknown-platform', Path(directory))

    def test_image_backend_selected_by_name_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'screen.png'
            Image.new('RGB', (640, 480)).save(path)
            with patch.dict(os.environ, KVM_BACKEND='image', KVM_SCREENSHOT=str(path), KVM_RUNTIME=directory):
                with connect() as computer:
                    self.assertEqual(computer.size(), (640, 480))
                    with self.assertRaisesRegex(RuntimeError, 'do not accept input'):
                        computer.click(100, 100)

    def test_connect_accepts_injected_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = FakeBackend(Path(directory))
            with connect(backend, directory) as computer:
                self.assertIs(computer.backend, backend)
                self.assertEqual(computer.gate.runtime, Path(directory))
