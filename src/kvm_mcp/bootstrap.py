"""Composition root: the only place selecting concrete platform adapters."""
import argparse
import os
from pathlib import Path



from kvm_automation.backends import load_backend


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
    from kvm_automation.ownership import InputGate
    gate = InputGate(args.runtime)
    if args.service == 'mcp':
        from .app.mcp import create_mcp
        create_mcp(backend, gate).run(transport='stdio')
    else:
        import uvicorn
        from .app.web import make_app
        hosts = [host.strip() for host in os.environ.get('KVM_HOSTS', 'localhost,127.0.0.1').split(',')
                 if host.strip()]
        recordings = Path(os.environ.get('KVM_RECORDINGS',str(Path.home()/'.local/share/kvm-mcp/recordings')))
        uvicorn.run(make_app(os.environ['KVM_TOKEN'], backend, gate, hosts=hosts, recordings_dir=recordings),
                    host=os.environ.get('KVM_BIND', '0.0.0.0'),
                    port=int(os.environ.get('KVM_PORT', '8080')), proxy_headers=False,
                    ws_max_size=4096, ws_max_queue=8, access_log=False)


def mcp_main():
    main(service='mcp')


def web_main():
    main(service='web')
