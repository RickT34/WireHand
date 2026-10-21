"""Authenticated LAN viewer and exclusive WebSocket HID control."""
import asyncio
from anyio import CancelScope
from contextlib import asynccontextmanager, suppress
import hashlib
import hmac
import json
import io
import logging
from collections import deque
from pathlib import Path
import time
import re
import zipfile
import ipaddress
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from kvm_automation.contracts import Backend
from .control import Controller
from kvm_automation.ownership import InputGate
from kvm_automation import Recording
from .rtc import RTCManager
from .h264 import H264Relay, serve_h264

STATIC = Path(__file__).parent / 'static'
LOGIN = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>登录 · KVM</title>
<link rel="stylesheet" href="/style.css"><body class="login-page"><main class="login-card">
<div class="brand-mark">K</div><p class="eyebrow">REMOTE CONSOLE</p>
<h1>你的电脑，近在眼前。</h1><p class="muted">登录控制台，查看设备画面并远程操作。</p>
<form action="/login" method="post"><label for="token">访问密码</label>
<input id="token" name="token" type="password" autocomplete="current-password" required autofocus>
<button class="primary" type="submit">打开控制台 <span>→</span></button></form>
<p class="muted small">{error}</p></main></body></html>'''


def make_app(token: str, backend: Backend, gate: InputGate, *, start_video=True, hosts=None, control_timeout=7, recordings_dir=None, trusted_proxies=()):
    if len(token) < 20:
        raise ValueError('KVM_TOKEN must contain at least 20 characters')
    hub = backend.create_video()
    controller = Controller(backend, gate, recordings_dir)
    control_mutex = asyncio.Lock()
    rtc = RTCManager(hub.encoded) if hub.encoded is not None else None
    h264 = H264Relay(hub.encoded) if hub.encoded is not None else None
    rtc_reports = deque(maxlen=20)

    def signature(value):
        return hmac.new(token.encode(), value.encode(), hashlib.sha256).hexdigest()

    def authenticated(connection):
        cookie = connection.cookies.get('kvm_session', '')
        try:
            timestamp, digest = cookie.split('.', 1)
            age = time.time() - int(timestamp)
            return 0 <= age < 43200 and hmac.compare_digest(digest, signature(timestamp))
        except (ValueError, TypeError):
            return False

    def same_origin(connection):
        origin = connection.headers.get('origin', '')
        scheme = 'https' if connection.url.scheme in ('https', 'wss') else 'http'
        return origin == f'{scheme}://{connection.headers.get("host", "")}'

    @asynccontextmanager
    async def lifespan(app):
        try:
            await asyncio.to_thread(controller.recover_stale)
        except (OSError, RuntimeError) as exc:
            logging.getLogger('uvicorn.error').warning('Input recovery: %s', exc)
        task = asyncio.create_task(hub.run()) if start_video else None
        try:
            yield
        finally:
            try:
                if rtc:
                    await rtc.close_all()
                if h264:
                    h264.close()
            finally:
                try:
                    if controller.owner is not None:
                        await asyncio.to_thread(controller.disconnect, controller.owner)
                finally:
                    if task:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task

    async def index(request):
        if not authenticated(request):
            return HTMLResponse(LOGIN.replace('{error}', ''), headers={'Cache-Control': 'no-store'})
        return FileResponse(STATIC / 'index.html', headers={'Cache-Control': 'no-store'})

    async def login(request: Request):
        if not same_origin(request):
            return JSONResponse({'error': 'Origin rejected'}, status_code=403)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 2048:
                return JSONResponse({'error': 'Request too large'}, status_code=413)
        candidate = parse_qs(data.decode(errors='replace')).get('token', [''])[0]
        if not hmac.compare_digest(candidate.encode(), token.encode()):
            await asyncio.sleep(.3)
            return HTMLResponse(LOGIN.replace('{error}', '密码不正确，请重试。'), status_code=401)
        timestamp = str(int(time.time()))
        response = RedirectResponse('/', status_code=303)
        response.set_cookie('kvm_session', timestamp + '.' + signature(timestamp),
                            max_age=43200, httponly=True, samesite='strict',
                            secure=request.url.scheme == 'https')
        return response

    async def logout(request):
        if not same_origin(request):
            return JSONResponse({'error': 'Origin rejected'}, status_code=403)
        response = RedirectResponse('/', status_code=303)
        response.delete_cookie('kvm_session')
        return response

    async def static_file(request):
        name = request.path_params['name']
        if name not in ('style.css', 'app.js', 'h264.js'):
            return JSONResponse({'error': 'Not found'}, status_code=404)
        return FileResponse(STATIC / name, headers={'Cache-Control': 'no-cache'})

    async def status(request):
        if not authenticated(request):
            return JSONResponse({'error': 'Login required'}, status_code=401)
        try:
            input_status = await asyncio.to_thread(backend.input_state)
        except (OSError, RuntimeError) as exc:
            input_status = {'connected': False, 'detail': str(exc)}
        return JSONResponse({'platform': backend.name, 'video': {**hub.status(),
                            'transport': 'h264-ws' if h264 else 'jpeg',
                            'transports': ['h264-ws','webrtc','jpeg'] if h264 else ['jpeg']},
                            'input': input_status, 'control': controller.status(), 'recording': controller.recording_status()},
                            headers={'Cache-Control': 'no-store'})

    async def rtc_offer(request):
        if not authenticated(request):
            return JSONResponse({'error': 'Login required'}, status_code=401)
        if not same_origin(request):
            return JSONResponse({'error': 'Origin rejected'}, status_code=403)
        if rtc is None:
            return JSONResponse({'error': 'WebRTC unavailable'}, status_code=409)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                return JSONResponse({'error': 'SDP too large'}, status_code=413)
        try:
            data = json.loads(body)
            if not isinstance(data, dict) or data.get('type') != 'offer' or not isinstance(data.get('sdp'), str):
                raise ValueError('Invalid offer')
            expiry = int(request.cookies['kvm_session'].split('.')[0]) + 43200
            result = await rtc.offer(data['sdp'], expiry)
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except (ValueError, AssertionError, RuntimeError, asyncio.TimeoutError) as exc:
            return JSONResponse({'error': str(exc) or 'Negotiation timed out'}, status_code=400)

    async def rtc_close(request):
        if not authenticated(request) or not same_origin(request):
            return JSONResponse({'error': 'Unauthorized'}, status_code=403)
        if rtc:
            await rtc.close(request.path_params['identity'])
        return JSONResponse({'ok': True})

    async def rtc_report(request):
        if not authenticated(request) or not same_origin(request):
            return JSONResponse({'error':'Unauthorized'},status_code=403)
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>4096:
                return JSONResponse({'error':'Report too large'},status_code=413)
        try:
            data=json.loads(raw)
            detail=data.get('diagnostic',{})
            if not isinstance(detail,dict):
                raise ValueError('Invalid diagnostic')
            allowed={'stage','connection','ice','bytesReceived','packetsReceived','packetsLost',
                     'framesDecoded','keyFramesDecoded','framesDropped','codec','codecParameters',
                     'decoder','hardwareDecoder','videoWidth','readyState','paused'}
            record={'time':int(time.time()),'reason':str(data.get('reason',''))[:200],
                    'browser':request.headers.get('user-agent','')[:250],
                    'diagnostic':{key:value for key,value in detail.items() if key in allowed
                                  and isinstance(value,(str,int,float,bool,type(None)))}}
            rtc_reports.append(record)
            logging.getLogger('uvicorn.error').info('RTC browser report %s',json.dumps(record,ensure_ascii=False))
            return JSONResponse({'ok':True})
        except (ValueError,AttributeError):
            return JSONResponse({'error':'Invalid report'},status_code=400)

    async def rtc_diagnostics(request):
        if not authenticated(request):
            return JSONResponse({'error':'Login required'},status_code=401)
        return JSONResponse(list(rtc_reports),headers={'Cache-Control':'no-store'})

    async def video_socket(ws):
        if not authenticated(ws) or not same_origin(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        sequence = -1
        try:
            while authenticated(ws):
                # A client requests the next frame only after decoding its predecessor.
                # Slow clients skip frames rather than accumulate queued pictures.
                if await asyncio.wait_for(ws.receive_text(), 15) != 'next':
                    break
                try:
                    sequence, frame = await asyncio.wait_for(hub.next_frame(sequence), 2)
                    await asyncio.wait_for(ws.send_bytes(frame), 3)
                except asyncio.TimeoutError:
                    await ws.send_json({'waiting': True})
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
            pass
        finally:
            with suppress(RuntimeError, WebSocketDisconnect):
                await ws.close()

    async def h264_socket(ws):
        if not authenticated(ws) or not same_origin(ws):
            await ws.close(code=1008)
            return
        if h264 is None:
            await ws.close(code=1003)
            return
        await serve_h264(ws,h264,authenticated)

    async def control_socket(ws):
        if not authenticated(ws) or not same_origin(ws):
            await ws.close(code=1008)
            return
        await ws.accept()
        owner = object()
        try:
            while authenticated(ws):
                message = await asyncio.wait_for(ws.receive_json(), control_timeout)
                if not isinstance(message, dict):
                    raise ValueError('Invalid message')
                error = ''
                async with control_mutex:
                    try:
                        kind = message.get('type')
                        if kind == 'ping':
                            pass
                        elif kind == 'take':
                            await asyncio.to_thread(controller.claim, owner)
                        elif kind == 'give':
                            await asyncio.to_thread(controller.give_back, owner)
                        elif kind == 'record_start':
                            await asyncio.to_thread(controller.start_recording, owner)
                        elif kind == 'record_stop':
                            await asyncio.to_thread(controller.stop_recording, owner)
                        else:
                            await asyncio.to_thread(controller.action, owner, message)
                    except (OSError, ValueError, RuntimeError) as exc:
                        error = str(exc)
                        if controller.owner is owner:
                            await asyncio.to_thread(controller.disconnect, owner)
                await ws.send_json({'ok': not error, 'error': error,
                                    'controlling': controller.owner is owner,
                                    'control': controller.status(),
                                    'recording': controller.recording_status()})
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, RuntimeError):
            pass
        finally:
            # Starlette may cancel this handler as the socket closes. Complete
            # device/lease cleanup even inside a cancelled AnyIO scope.
            with CancelScope(shield=True):
                async with control_mutex:
                    errors = await asyncio.to_thread(controller.disconnect, owner)
                    if errors:
                        logging.getLogger('uvicorn.error').warning('Input release: %s', '; '.join(errors))
            with suppress(RuntimeError, WebSocketDisconnect):
                await ws.close()

    async def recording_download(request):
        if not authenticated(request):
            return JSONResponse({'error':'Login required'},status_code=401)
        identity = request.path_params['identity']
        if not re.fullmatch('[a-f0-9]{32}',identity):
            return JSONResponse({'error':'Not found'},status_code=404)
        directory = controller.recordings_dir/identity
        def archive():
            flow = Recording.load(directory,allow_incomplete=True)
            if flow.data['status']=='recording':
                raise ValueError('Stop recording before downloading')
            output = io.BytesIO()
            with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
                for name in ('recording.json','replay.py'):
                    bundle.write(directory/name,f'{identity}/{name}')
                for target in flow.data['targets'].values():
                    bundle.write(directory/target['image'],f'{identity}/{target["image"]}')
            return output.getvalue()
        try:
            data = await asyncio.to_thread(archive)
        except FileNotFoundError:
            return JSONResponse({'error':'Not found'},status_code=404)
        except ValueError as exc:
            return JSONResponse({'error':str(exc)},status_code=409)
        return Response(data,media_type='application/zip',headers={
            'Content-Disposition':f'attachment; filename="kvm-recording-{identity}.zip"',
            'Cache-Control':'no-store'})

    app = Starlette(lifespan=lifespan, routes=[
        Route('/', index), Route('/login', login, methods=['POST']),
        Route('/logout', logout, methods=['POST']), Route('/api/status', status),
        Route('/api/rtc/offer', rtc_offer, methods=['POST']),
        Route('/api/rtc/report', rtc_report, methods=['POST']),
        Route('/api/rtc/diagnostics', rtc_diagnostics),
        Route('/api/recordings/{identity}/download',recording_download),
        Route('/api/rtc/{identity}', rtc_close, methods=['DELETE']),
        Route('/{name}', static_file), WebSocketRoute('/ws/video', video_socket),
        WebSocketRoute('/ws/h264', h264_socket),
        WebSocketRoute('/ws/control', control_socket)])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts or ['localhost', '127.0.0.1'])
    if trusted_proxies:
        # No wildcard: trust forwarding headers only from explicitly named
        # reverse-proxy addresses/networks, never from arbitrary clients.
        for address in trusted_proxies:
            ipaddress.ip_network(address,strict=False)
        app.add_middleware(ProxyHeadersMiddleware,trusted_hosts=list(trusted_proxies))
    app.state.controller = controller
    return app

