"""Authenticated LAN viewer and exclusive WebSocket HID control."""
import asyncio
from anyio import CancelScope
from contextlib import asynccontextmanager, suppress
import hashlib
import hmac
import json
import logging
from collections import deque
from pathlib import Path
import time
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from kvm_mcp.contracts import Backend
from .control import Controller
from .ownership import InputGate
from .rtc import RTCManager

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


def make_app(token: str, backend: Backend, gate: InputGate, *, start_video=True, hosts=None, control_timeout=7):
    if len(token) < 20:
        raise ValueError('KVM_TOKEN must contain at least 20 characters')
    hub = backend.create_video()
    controller = Controller(backend, gate)
    control_mutex = asyncio.Lock()
    rtc = RTCManager(hub.encoded) if hub.encoded is not None else None
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
        if name not in ('style.css', 'app.js'):
            return JSONResponse({'error': 'Not found'}, status_code=404)
        return FileResponse(STATIC / name, headers={'Cache-Control': 'no-cache'})

    async def status(request):
        if not authenticated(request):
            return JSONResponse({'error': 'Login required'}, status_code=401)
        try:
            input_status = await asyncio.to_thread(backend.input_state)
        except (OSError, RuntimeError) as exc:
            input_status = {'connected': False, 'detail': str(exc)}
        return JSONResponse({'platform': backend.name, 'video': {**hub.status(), 'transport': 'webrtc' if rtc else 'jpeg'}, 'input': input_status, 'control': controller.status()},
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
                        else:
                            await asyncio.to_thread(controller.action, owner, message)
                    except (OSError, ValueError, RuntimeError) as exc:
                        error = str(exc)
                        if controller.owner is owner:
                            await asyncio.to_thread(controller.disconnect, owner)
                await ws.send_json({'ok': not error, 'error': error,
                                    'controlling': controller.owner is owner,
                                    'control': controller.status()})
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

    app = Starlette(lifespan=lifespan, routes=[
        Route('/', index), Route('/login', login, methods=['POST']),
        Route('/logout', logout, methods=['POST']), Route('/api/status', status),
        Route('/api/rtc/offer', rtc_offer, methods=['POST']),
        Route('/api/rtc/report', rtc_report, methods=['POST']),
        Route('/api/rtc/diagnostics', rtc_diagnostics),
        Route('/api/rtc/{identity}', rtc_close, methods=['DELETE']),
        Route('/{name}', static_file), WebSocketRoute('/ws/video', video_socket),
        WebSocketRoute('/ws/control', control_socket)])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts or ['localhost', '127.0.0.1'])
    app.state.controller = controller
    return app

