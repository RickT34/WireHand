"""WebRTC signaling, diagnostics and authenticated video sockets."""
import asyncio
from contextlib import suppress
import json
import logging
import time

from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from ..video.h264 import serve_h264


async def rtc_offer(request):
    services = request.app.state.services
    if not services.auth.authenticated(request):
        return JSONResponse({'error': 'Login required'}, status_code=401)
    if not services.auth.same_origin(request):
        return JSONResponse({'error': 'Origin rejected'}, status_code=403)
    if services.rtc is None:
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
        result = await services.rtc.offer(data['sdp'], expiry)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})
    except (ValueError, AssertionError, RuntimeError, asyncio.TimeoutError) as exc:
        return JSONResponse({'error': str(exc) or 'Negotiation timed out'}, status_code=400)


async def rtc_close(request):
    services = request.app.state.services
    if not services.auth.authenticated(request) or not services.auth.same_origin(request):
        return JSONResponse({'error': 'Unauthorized'}, status_code=403)
    if services.rtc:
        await services.rtc.close(request.path_params['identity'])
    return JSONResponse({'ok': True})


async def rtc_report(request):
    services = request.app.state.services
    if not services.auth.authenticated(request) or not services.auth.same_origin(request):
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
        services.rtc_reports.append(record)
        logging.getLogger('uvicorn.error').info('RTC browser report %s',json.dumps(record,ensure_ascii=False))
        return JSONResponse({'ok':True})
    except (ValueError,AttributeError):
        return JSONResponse({'error':'Invalid report'},status_code=400)


async def rtc_diagnostics(request):
    services = request.app.state.services
    if not services.auth.authenticated(request):
        return JSONResponse({'error':'Login required'},status_code=401)
    return JSONResponse(list(services.rtc_reports),headers={'Cache-Control':'no-store'})


async def video_socket(ws):
    services = ws.app.state.services
    if not services.auth.authenticated(ws) or not services.auth.same_origin(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    sequence = -1
    try:
        while services.auth.authenticated(ws):
            # A client requests the next frame only after decoding its predecessor.
            # Slow clients skip frames rather than accumulate queued pictures.
            if await asyncio.wait_for(ws.receive_text(), 15) != 'next':
                break
            try:
                sequence, frame = await asyncio.wait_for(services.hub.next_frame(sequence), 2)
                await asyncio.wait_for(ws.send_bytes(frame), 3)
            except asyncio.TimeoutError:
                await ws.send_json({'waiting': True})
    except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError):
        pass
    finally:
        with suppress(RuntimeError, WebSocketDisconnect):
            await ws.close()


async def h264_socket(ws):
    services = ws.app.state.services
    if not services.auth.authenticated(ws) or not services.auth.same_origin(ws):
        await ws.close(code=1008)
        return
    if services.h264 is None:
        await ws.close(code=1003)
        return
    await serve_h264(ws,services.h264,services.auth.authenticated)


