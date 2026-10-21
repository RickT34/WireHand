"""Compose web services, routes, middleware and resource lifecycle."""
import asyncio
from contextlib import asynccontextmanager, suppress
import ipaddress
import logging

from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Route, WebSocketRoute
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from kvm.core.contracts import Backend
from kvm.core.ownership import InputGate
from .auth import SessionAuth
from .control import Controller
from .services import WebServices
from .routes import control, pages, recordings, status, video
from .video.h264 import H264Relay
from .video.rtc import RTCManager


def make_app(token: str, backend: Backend, gate: InputGate, *, start_video=True,
             hosts=None, control_timeout=7, recordings_dir=None, trusted_proxies=()):
    auth = SessionAuth(token)
    hub = backend.create_video()
    services = WebServices(
        auth=auth, backend=backend, hub=hub,
        controller=Controller(backend, gate, recordings_dir),
        rtc=RTCManager(hub.encoded) if hub.encoded is not None else None,
        h264=H264Relay(hub.encoded) if hub.encoded is not None else None,
        control_timeout=control_timeout,
    )

    @asynccontextmanager
    async def lifespan(app):
        try:
            await asyncio.to_thread(services.controller.recover_stale)
        except (OSError, RuntimeError) as exc:
            logging.getLogger('uvicorn.error').warning('Input recovery: %s', exc)
        task = asyncio.create_task(services.hub.run()) if start_video else None
        try:
            yield
        finally:
            try:
                if services.rtc:
                    await services.rtc.close_all()
                if services.h264:
                    services.h264.close()
            finally:
                try:
                    if services.controller.owner is not None:
                        await asyncio.to_thread(services.controller.disconnect, services.controller.owner)
                finally:
                    if task:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task


    app = Starlette(lifespan=lifespan, routes=[
        Route('/', pages.index),
        Route('/login', auth.login, methods=['POST']),
        Route('/logout', auth.logout, methods=['POST']),
        Route('/api/status', status.status),
        Route('/api/rtc/offer', video.rtc_offer, methods=['POST']),
        Route('/api/rtc/report', video.rtc_report, methods=['POST']),
        Route('/api/rtc/diagnostics', video.rtc_diagnostics),
        Route('/api/rtc/{identity}', video.rtc_close, methods=['DELETE']),
        Route('/api/recordings/{identity}/download', recordings.recording_download),
        Route('/{name}', pages.static_file),
        WebSocketRoute('/ws/video', video.video_socket),
        WebSocketRoute('/ws/h264', video.h264_socket),
        WebSocketRoute('/ws/control', control.control_socket),
    ])
    app.state.services = services
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts or ['localhost', '127.0.0.1'])
    if trusted_proxies:
        # Trust forwarding headers only from explicitly configured proxy networks.
        for address in trusted_proxies:
            ipaddress.ip_network(address, strict=False)
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=list(trusted_proxies))
    return app
