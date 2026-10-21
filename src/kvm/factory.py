"""Compose an SDK session with a selected platform; no application imports."""
import os
from pathlib import Path


def connect(backend=None, runtime=None, **options):
    from kvm.automation import Computer
    from kvm.platforms.registry import load_backend

    runtime = Path(runtime or os.environ.get('KVM_RUNTIME', '/run/kvm-mcp'))
    spec = backend if backend is not None else os.environ.get('KVM_BACKEND')
    if spec is None:
        raise ValueError('Set KVM_BACKEND or pass a backend name/object to connect')
    device = load_backend(spec, runtime) if isinstance(spec, str) else spec
    return Computer(device, runtime, **options)
