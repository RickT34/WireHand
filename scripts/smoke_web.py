"""Verify live HTTP/WebSocket transport without sending physical HID input."""
import argparse
import asyncio
import io
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import time

import httpx
from PIL import Image
from websockets.asyncio.client import connect


async def main(origin, frames, live=False):
    token = next(line.split(': ',1)[1] for line in Path('web-access.txt').read_text().splitlines()
                 if line.startswith('Password: '))
    origin = origin.rstrip('/')
    parts = urlsplit(origin)
    socket_origin = urlunsplit(('wss' if parts.scheme == 'https' else 'ws', parts.netloc, '', '', ''))
    async with httpx.AsyncClient(base_url=origin, trust_env=False) as client:
        deadline = time.monotonic() + 10
        while True:
            try:
                anonymous = await client.get('/api/status')
                break
            except httpx.ConnectError:
                if time.monotonic() >= deadline:
                    raise
                await asyncio.sleep(.2)
        assert anonymous.status_code == 401
        response = await client.post('/login', data={'token':token}, headers={'Origin':origin})
        assert response.status_code == 303, response.status_code
        page = await client.get('/')
        assert '设备画面' in page.text
        status = (await client.get('/api/status')).json()
        cookie = '; '.join(f'{k}={v}' for k,v in client.cookies.items())
        headers = {'Cookie':cookie}
        async with connect(f'{socket_origin}/ws/control', origin=origin,
                           additional_headers=headers, proxy=None) as ws:
            # Viewer cannot send HID, even by bypassing the UI controls.
            await ws.send(json.dumps({'type':'key','code':'KeyA','down':True}))
            denied = json.loads(await ws.recv())
            assert not denied['ok'] and not denied['controlling'], denied
        async with connect(f'{socket_origin}/ws/video', origin=origin,
                           additional_headers=headers, proxy=None, max_size=16*1024*1024) as ws:
            if frames:
                assert live or status['video'].get('test_source', False), 'Use --live to test physical HDMI'
                count, sizes = 0, 0
                start = time.monotonic()
                while count < frames:
                    await ws.send('next')
                    frame = await asyncio.wait_for(ws.recv(), 6)
                    assert isinstance(frame, bytes), frame
                    with Image.open(io.BytesIO(frame)) as image:
                        image.load()
                        assert image.size == (status['video']['width'],status['video']['height'])
                    sizes += len(frame)
                    count += 1
                elapsed = time.monotonic() - start
                print(json.dumps({'frames':count,'seconds':round(elapsed,3),
                    'received_fps':round(count/elapsed,1),'mean_jpeg_bytes':sizes//count}))
                # Slow consumer: there should be no unsolicited queued frames.
                try:
                    await asyncio.wait_for(ws.recv(), .3)
                    raise AssertionError('Server sent a frame without client demand')
                except asyncio.TimeoutError:
                    pass
            else:
                await ws.send('next')
                reply = await asyncio.wait_for(ws.recv(), 6)
                assert isinstance(reply, str) and json.loads(reply)['waiting'], reply
        print(json.dumps(status,ensure_ascii=False,indent=2))
        print('Authenticated HTTP + viewer authorization + video WebSocket passed')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--origin',required=True)
    parser.add_argument('--frames',type=int,default=0)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    asyncio.run(main(args.origin,args.frames,args.live))
