"""Authenticated H.264 WS/WSS receive/decode probe, including slow consumers."""
import argparse
import asyncio
import base64
import io
import json
from pathlib import Path
import ssl
import time
from urllib.parse import urlsplit,urlunsplit

import av
import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from kvm_mcp.app.h264 import HEADER


async def main(origin,token_file,seconds=5,mode='annexb',ca_file=None,slow=False,ack_delay_ms=0,bandwidth_mbps=0):
    token=token_file.read_text().strip()
    if 'Password: ' in token:
        token=next(line.split(': ',1)[1] for line in token.splitlines() if line.startswith('Password: '))
    origin=origin.rstrip('/')
    context=ssl.create_default_context(cafile=str(ca_file)) if ca_file else ssl.create_default_context()
    parts=urlsplit(origin)
    ws_url=urlunsplit(('wss' if parts.scheme=='https' else 'ws',parts.netloc,'/ws/h264',f'format={mode}',''))
    async with httpx.AsyncClient(base_url=origin,verify=context,trust_env=False,timeout=10) as client:
        response=await client.post('/login',data={'token':token},headers={'Origin':origin})
        assert response.status_code==303, response.text
        if parts.scheme=='https':assert 'Secure' in response.headers['set-cookie']
        cookie='; '.join(f'{k}={v}' for k,v in client.cookies.items())
        kwargs={'origin':origin,'additional_headers':{'Cookie':cookie},'proxy':None,
                'max_size':2*1024*1024,'compression':None}
        if parts.scheme=='https':kwargs['ssl']=context
        decoder=None;fragments=[];generation=None;frames=0;messages=0;total=0;first=None;first_packet=None
        started=time.monotonic();stats={}
        async with connect(ws_url,**kwargs) as ws:
            acknowledgements=set()
            async def acknowledge(sequence):
                await asyncio.sleep(ack_delay_ms/1000)
                try:await ws.send(json.dumps({'type':'ack','sequence':sequence}))
                except ConnectionClosed:pass
            try:
                while time.monotonic()-started<seconds:
                    message=await asyncio.wait_for(ws.recv(),6)
                    if isinstance(message,str):
                        item=json.loads(message)
                        if item['type']=='config':
                            generation=item['generation']
                            if mode=='annexb':decoder=av.CodecContext.create('h264','r')
                            else:fragments=[base64.b64decode(item['init'])]
                        elif item['type']=='stats':stats=item
                        continue
                    magic,key,sequence,epoch,pts=HEADER.unpack(message[:HEADER.size])
                    assert magic==b'KVM1' and generation==epoch
                    first_packet=first_packet or time.monotonic();messages+=1;total+=len(message)
                    if bandwidth_mbps:
                        await asyncio.sleep(len(message)*8/(bandwidth_mbps*1000000))
                    if mode=='annexb':
                        packet=av.Packet(message[HEADER.size:]);packet.pts=pts
                        decoded=decoder.decode(packet)
                        if decoded:
                            first=first or time.monotonic();frames+=len(decoded)
                    else:fragments.append(message[HEADER.size:])
                    if not slow:
                        task=asyncio.create_task(acknowledge(sequence));acknowledgements.add(task)
                        task.add_done_callback(acknowledgements.discard)
            except ConnectionClosed:
                if not slow:raise
            finally:
                for task in acknowledgements:task.cancel()
                await asyncio.gather(*acknowledgements,return_exceptions=True)
            if slow:
                assert 1<=messages<=8, messages
                assert time.monotonic()-started<seconds, 'Slow consumer was not closed'
            elif mode=='mp4':
                with av.open(io.BytesIO(b''.join(fragments)),format='mp4') as container:
                    frames=sum(1 for _ in container.decode(video=0))
                assert frames>0
            else:assert frames>0
        elapsed=time.monotonic()-started
        return {'transport':parts.scheme,'format':mode,'messages':messages,'decoded_frames':frames,
                'seconds':round(elapsed,3),'decoded_fps':round(frames/max(.001,time.monotonic()-(first or first_packet or started)),1),
                'payload_mbps':round(total*8/elapsed/1000000,3),
                'first_decoded_frame_seconds':round(first-started,3) if first else None,
                'slow_client_closed':slow,'ack_delay_ms':ack_delay_ms,'consumer_limit_mbps':bandwidth_mbps,
                'last_server_stats':stats}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin',required=True)
    parser.add_argument('--token-file',type=Path,default=Path('web-access.txt'))
    parser.add_argument('--seconds',type=float,default=5)
    parser.add_argument('--format',choices=['annexb','mp4'],default='annexb')
    parser.add_argument('--ca-file',type=Path)
    parser.add_argument('--slow',action='store_true')
    parser.add_argument('--ack-delay-ms',type=float,default=0)
    parser.add_argument('--bandwidth-mbps',type=float,default=0)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.ack_delay_ms<0 or args.bandwidth_mbps<0:parser.error('Delays and bandwidth must be nonnegative')
    result=asyncio.run(main(args.origin,args.token_file,args.seconds,args.format,args.ca_file,args.slow,args.ack_delay_ms,args.bandwidth_mbps))
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
