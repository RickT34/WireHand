"""Verify live browser control release; sends no key-down or mouse-click events."""
import argparse
import asyncio
import json
from pathlib import Path
import time
from urllib.parse import urlsplit,urlunsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed


async def main(origin, token_file, check_timeout):
    token = next(line.split(': ',1)[1] for line in token_file.read_text().splitlines()
                 if line.startswith('Password: '))
    origin = origin.rstrip('/')
    parts = urlsplit(origin)
    socket_url = urlunsplit(('wss' if parts.scheme=='https' else 'ws',parts.netloc,'/ws/control','',''))
    async with httpx.AsyncClient(base_url=origin,trust_env=False,timeout=10) as client:
        response = await client.post('/login',data={'token':token},headers={'Origin':origin})
        assert response.status_code==303, response.status_code
        response = await client.get('/api/status')
        response.raise_for_status()
        initial = response.json()['control']
        if initial['connected'] or initial['manual']:
            raise RuntimeError('Control is already owned or paused; do not take over another session')
        headers = {'Cookie':'; '.join(f'{key}={value}' for key,value in client.cookies.items())}

        async def assert_returned():
            deadline = time.monotonic()+3
            while True:
                response = await client.get('/api/status')
                response.raise_for_status()
                control = response.json()['control']
                if not control['manual'] and not control['connected']:
                    return control
                if time.monotonic()>=deadline:
                    raise AssertionError(control)
                await asyncio.sleep(.05)

        async with connect(socket_url,origin=origin,additional_headers=headers,proxy=None) as ws:
            await ws.send(json.dumps({'type':'take'}))
            claimed = json.loads(await asyncio.wait_for(ws.recv(),5))
            assert claimed['ok'] and claimed['controlling'] and claimed['control']['manual'], claimed
        print(json.dumps({'test':'disconnect','control':await assert_returned()}))

        if check_timeout:
            async with connect(socket_url,origin=origin,additional_headers=headers,proxy=None) as ws:
                await ws.send(json.dumps({'type':'take'}))
                claimed = json.loads(await asyncio.wait_for(ws.recv(),5))
                assert claimed['ok'] and claimed['controlling'], claimed
                started = time.monotonic()
                try:
                    message = await asyncio.wait_for(ws.recv(),10)
                    raise AssertionError('Expected idle control connection to close: '+str(message))
                except ConnectionClosed:
                    elapsed = time.monotonic()-started
                assert 6 <= elapsed <= 10, elapsed
            print(json.dumps({'test':'heartbeat_timeout','seconds':round(elapsed,3),
                              'control':await assert_returned()}))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin',required=True)
    parser.add_argument('--token-file',type=Path,default=Path('web-access.txt'))
    parser.add_argument('--check-timeout',action='store_true')
    args=parser.parse_args()
    asyncio.run(main(args.origin,args.token_file,args.check_timeout))
