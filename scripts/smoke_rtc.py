"""Real authenticated WebRTC receiver: H.264 decode, frame rate, and RTP loss."""
import argparse
import asyncio
import json
from pathlib import Path
import time

import httpx
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.jitterbuffer import JitterBuffer


async def main(seconds, origin, jitter_packets=1024):
    if not 128 <= jitter_packets <= 8192 or jitter_packets & (jitter_packets-1):
        raise ValueError('jitter_packets must be a power of two from 128 to 8192')
    token = next(line.split(': ',1)[1] for line in Path('web-access.txt').read_text().splitlines()
                 if line.startswith('Password: '))
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    transceiver = peer.addTransceiver('video', direction='recvonly')
    # Diagnostic receiver only: aiortc's default 128-packet video buffer can
    # discard the beginning of large MPP IDRs despite zero network packet loss.
    # aiortc 1.x has no public setter; revisit this attribute when upgrading.
    transceiver.receiver._RTCRtpReceiver__jitter_buffer = JitterBuffer(capacity=jitter_packets, is_video=True)
    incoming = asyncio.Future()
    identity = None

    @peer.on('track')
    def on_track(track):
        if not incoming.done():
            incoming.set_result(track)

    async with httpx.AsyncClient(base_url=origin,trust_env=False,timeout=15) as client:
        result = await client.post('/login',data={'token':token},headers={'Origin':origin})
        assert result.status_code == 303
        try:
            await peer.setLocalDescription(await peer.createOffer())
            started = time.monotonic()
            response = await client.post('/api/rtc/offer',headers={'Origin':origin},
                                         json={'type':'offer','sdp':peer.localDescription.sdp})
            assert response.status_code == 200, response.text
            answer = response.json()
            identity = answer['id']
            await peer.setRemoteDescription(RTCSessionDescription(answer['sdp'],answer['type']))
            track = await asyncio.wait_for(incoming,10)
            frame = await asyncio.wait_for(track.recv(),10)
            first_delay = time.monotonic()-started
            Path('artifacts').mkdir(exist_ok=True)
            state = (await client.get('/api/status')).json()
            suffix = 'synthetic' if state['video'].get('test_source', False) else 'live'
            frame.to_image().save(f'artifacts/webrtc-{suffix}-frame.jpg')
            begin, count = time.monotonic(), 1
            while time.monotonic()-begin < seconds:
                frame = await asyncio.wait_for(track.recv(),5)
                assert (frame.width, frame.height) == (state['video']['width'], state['video']['height'])
                count += 1
            elapsed = time.monotonic()-begin
            reports = await peer.getStats()
            received = [r for r in reports.values() if r.type=='inbound-rtp']
            status = (await client.get('/api/status')).json()
            metrics = {'decoded_frames':count, 'seconds':round(elapsed,3), 'jitter_buffer_packets':jitter_packets,
                       'decoded_fps':round((count-1)/elapsed,1),
                       'first_frame_after_offer_seconds':round(first_delay,3),
                       'connection':peer.connectionState,
                       'rtp':[{'packetsReceived':r.packetsReceived,'packetsLost':r.packetsLost,
                               'jitter':r.jitter} for r in received], 'server_video':status['video']}
            print(json.dumps(metrics,indent=2))
            Path(f'artifacts/webrtc-{suffix}-metrics.json').write_text(json.dumps(metrics,indent=2))
        except Exception:
            print('Receiver state:', peer.connectionState, peer.iceConnectionState)
            print({key: vars(value) for key,value in (await peer.getStats()).items()})
            raise
        finally:
            await peer.close()
            if identity:
                await client.delete(f'/api/rtc/{identity}',headers={'Origin':origin})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds',type=int,default=10)
    parser.add_argument('--origin',default='http://localhost:8080')
    parser.add_argument('--jitter-packets',type=int,default=1024)
    args = parser.parse_args()
    asyncio.run(main(args.seconds,args.origin,args.jitter_packets))
