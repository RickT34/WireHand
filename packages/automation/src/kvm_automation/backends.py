"""Load optional device adapters without importing application frameworks."""
import importlib
from importlib.metadata import entry_points
from pathlib import Path

from .contracts import Backend


def load_backend(spec: str, runtime: Path) -> Backend:
    if ':' in spec:
        module, factory = spec.split(':', 1)
        return getattr(importlib.import_module(module), factory)(runtime=runtime)
    matches = list(entry_points(group='kvm_automation.backends', name=spec))
    if len(matches) != 1:
        raise ValueError(f'Install the backend plugin for {spec!r}, or specify module:factory')
    return matches[0].load()(runtime=runtime)
