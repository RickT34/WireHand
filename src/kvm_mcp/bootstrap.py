"""Composition root: the only place selecting concrete platform adapters."""
import argparse
import importlib
import os
from pathlib import Path

from .contracts import Backend


def load_backend(spec: str, runtime: Path) -> Backend:
    if spec == 'rock5b-plus':
        spec = 'kvm_mcp.platforms.rock5b_plus.backend:Rock5BPlusBackend'
    module, separator, factory = spec.partition(':')
    if not separator or not module or not factory:
        raise ValueError('Backend must be rock5b-plus or module:factory')
    return getattr(importlib.import_module(module), factory)(runtime=runtime)


def main(argv=None, *, service=None):
    parser = argparse.ArgumentParser(description='KVM MCP and web applications')
    if service is None:
        parser.add_argument('service', choices=['mcp', 'web'])
    else:
        parser.set_defaults(service=service)
    parser.add_argument('--backend', default=os.environ.get('KVM_BACKEND'),
                        help='rock5b-plus or module:factory; alternatively set KVM_BACKEND')
    parser.add_argument('--runtime', type=Path,
                        default=Path(os.environ.get('KVM_RUNTIME', '/run/kvm-mcp')),
                        help='Shared runtime directory for all processes controlling this target')
    args = parser.parse_args(argv)
    if not args.backend:
        parser.error('--backend or KVM_BACKEND is required')
    backend = load_backend(args.backend, args.runtime)
    from .app.ownership import InputGate
    gate = InputGate(args.runtime)
    if args.service == 'mcp':
        from .app.mcp import create_mcp
        create_mcp(backend, gate).run(transport='stdio')
    else:
        import uvicorn
        from .app.web import make_app
        hosts = [host.strip() for host in os.environ.get('KVM_HOSTS', 'localhost,127.0.0.1').split(',')
                 if host.strip()]
        uvicorn.run(make_app(os.environ['KVM_TOKEN'], backend, gate, hosts=hosts),
                    host=os.environ.get('KVM_BIND', '0.0.0.0'),
                    port=int(os.environ.get('KVM_PORT', '8080')), proxy_headers=False,
                    ws_max_size=4096, ws_max_queue=8, access_log=False)


def mcp_main():
    main(service='mcp')


def web_main():
    main(service='web')
