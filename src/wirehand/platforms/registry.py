"""Explicit, lazy selection of built-in or module:factory platform adapters."""
from importlib import import_module
from pathlib import Path

BUILTINS = {
    'docker-desktop': 'wirehand.platforms.docker_desktop.backend:DockerDesktopBackend',
    'rock5b-plus': 'wirehand.platforms.rock5b_plus.backend:Rock5BPlusBackend',
    'image': 'wirehand.platforms.image:ImageBackend',
}


def load_backend(spec: str, runtime: Path):
    factory_path = BUILTINS.get(spec, spec)
    module, separator, attribute = factory_path.partition(':')
    if not separator or not module or not attribute:
        raise ValueError(f'Unknown backend {spec!r}; choose {", ".join(BUILTINS)} or module:factory')
    factory = getattr(import_module(module), attribute)
    return factory(runtime=Path(runtime))
