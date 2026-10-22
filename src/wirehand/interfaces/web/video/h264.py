"""Authenticated WebSocket media plumbing; no capture/device dependencies.

KVM1 binary messages: !4sB3xIIQ header (key flag, sequence, generation,
relative microsecond timestamp), followed by Annex-B or fragmented MP4 bytes.
Clients acknowledge each message after drawing (WebCodecs) or append (MSE).
"""
import asyncio
import base64
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
from fractions import Fraction
import io
import re
import struct
import time

import av
from anyio import CancelScope
from starlette.websockets import WebSocketDisconnect

HEADER = struct.Struct('!4sB3xIIQ')
MAX_FRAME_BYTES = 1024 * 1024


def nal_units(data):
    return [part for part in re.split(b'\x00\x00\x00?\x01', data)[1:] if part]


@dataclass(frozen=True)
class EncodedFrame:
    data: bytes
    pts: int
    keyframe: bool
    generation: int
    codec: str
    created: float


class Subscription:
    def __init__(self, relay):
        self.relay = relay
        self.queue = asyncio.Queue(maxsize=2)
        self.wait_keyframe = True
        self.dropped = 0

    def reset(self):
        while not self.queue.empty():
            self.queue.get_nowait()
            self.dropped += 1
        self.wait_keyframe = True

    def push(self, frame):
        if self.queue.full():
            self.reset()
        if self.wait_keyframe:
            if not frame.keyframe:
                self.dropped += 1
                return
            self.wait_keyframe = False
        self.queue.put_nowait(frame)

    async def next_frame(self, max_age=.15):
        while True:
            frame = await self.queue.get()
            if frame.generation != self.relay.generation or time.monotonic()-frame.created > max_age:
                self.dropped += 1
                self.reset()
                continue
            return frame

    def close(self):
        self.relay.clients.discard(self)
        self.reset()


class H264Relay:
    """Cache SPS/PPS and attach them to IDRs, including for late subscribers."""
    def __init__(self, hub):
        self.hub = hub
        self.clients = set()
        self.parameters = {}
        self.generation = 1
        self.codec = None
        hub.tracks.add(self)

    def subscribe(self):
        if len(self.clients) >= 4:
            raise RuntimeError('最多同时连接四个 H.264 查看者')
        client = Subscription(self)
        self.clients.add(client)
        return client

    def push(self, data, pts, keyframe):
        if len(data) > MAX_FRAME_BYTES:
            self.reset()
            return
        units = nal_units(data)
        present = {unit[0] & 31 for unit in units}
        changed = False
        for unit in units:
            kind = unit[0] & 31
            if kind in (7,8):
                if kind in self.parameters and self.parameters[kind] != unit:
                    changed = True
                self.parameters[kind] = unit
        if changed:
            self.generation += 1
            for client in tuple(self.clients):
                client.reset()
        sps = self.parameters.get(7)
        if not sps or len(sps)<4 or 8 not in self.parameters:
            return
        self.codec = 'avc1.' + sps[1:4].hex()
        keyframe = 5 in present
        if keyframe:
            prefix = b''.join(b'\x00\x00\x00\x01'+self.parameters[kind]
                              for kind in (7,8) if kind not in present)
            data = prefix + data
        if not present.intersection((1,5)):
            return
        frame = EncodedFrame(data,pts,keyframe,self.generation,self.codec,time.monotonic())
        for client in tuple(self.clients):
            client.push(frame)

    def reset(self):
        self.parameters.clear()
        self.codec = None
        self.generation += 1
        for client in tuple(self.clients):
            client.reset()

    def close(self):
        self.hub.tracks.discard(self)
        for client in tuple(self.clients):
            client.close()


class SendWindow:
    """Bound data already handed to TCP as well as unsent application frames."""
    def __init__(self, max_frames=8, max_bytes=128*1024, ack_timeout=1.5):
        self.max_frames, self.max_bytes, self.ack_timeout = max_frames, max_bytes, ack_timeout
        self.pending = OrderedDict()
        self.last_sequence = 0
        self.changed = asyncio.Event()
        self.ack_ms = None

    async def ready(self, reserve=0):
        while len(self.pending)>=self.max_frames or (self.pending and sum(size for _,size in self.pending.values())+reserve>self.max_bytes):
            self.changed.clear()
            deadline = next(iter(self.pending.values()))[0]+self.ack_timeout
            await asyncio.wait_for(self.changed.wait(),max(.001,deadline-time.monotonic()))
        if self.pending and time.monotonic()-next(iter(self.pending.values()))[0] > self.ack_timeout:
            raise TimeoutError('Video acknowledgement timed out')

    def sent(self, size):
        self.last_sequence += 1
        self.pending[self.last_sequence] = (time.monotonic(),size)
        return self.last_sequence

    def ack(self, sequence):
        if type(sequence) is not int or not 1 <= sequence <= self.last_sequence:
            raise ValueError('Invalid video acknowledgement')
        item = self.pending.pop(sequence,None)
        if item:
            self.ack_ms = round((time.monotonic()-item[0])*1000,1)
            self.changed.set()


class Buffer:
    def __init__(self):
        self.data = bytearray()
    def write(self, data):
        self.data.extend(data)
        return len(data)
    def writable(self):
        return True
    def drain(self):
        data = bytes(self.data)
        self.data.clear()
        return data


class FragmentMuxer:
    """Remux Annex-B packets to fMP4 without opening a software encoder."""
    def __init__(self, keyframe):
        self.buffer = Buffer()
        with av.open(io.BytesIO(keyframe.data),format='h264') as source:
            self.output = av.open(self.buffer,mode='w',format='mp4',options={
                'movflags':'empty_moov+default_base_moof+frag_every_frame', 'flush_packets':'1'})
            self.stream = self.output.add_stream_from_template(source.streams.video[0])
            self.stream.time_base = Fraction(1,90000)
            self.output.start_encoding()
        self.init = self.buffer.drain()
        self.origin = keyframe.pts
        self.last_pts = -1

    def mux(self, frame):
        pts = max(self.last_pts+1,frame.pts-self.origin)
        packet = av.Packet(frame.data)
        packet.pts = packet.dts = pts
        packet.duration = 1500 if self.last_pts<0 else max(1,pts-self.last_pts)
        packet.time_base = Fraction(1,90000)
        packet.is_keyframe = frame.keyframe
        packet.stream = self.stream
        self.output.mux(packet)
        self.last_pts = pts
        return self.buffer.drain()

    def close(self):
        self.output.close()
        return self.buffer.drain()


async def serve_h264(ws, relay, authenticated, *, ack_timeout=1.5, max_age=.15):
    mode = ws.query_params.get('format','annexb')
    if mode not in ('annexb','mp4'):
        await ws.close(code=1008)
        return
    try:
        client = relay.subscribe()
    except RuntimeError:
        await ws.close(code=1013)
        return
    window = SendWindow(ack_timeout=ack_timeout)
    muxer = None
    configured = None
    generation = 0
    origin = None
    resync = False

    async def receive():
        nonlocal resync
        while authenticated(ws):
            message = await asyncio.wait_for(ws.receive_json(),10)
            if not isinstance(message,dict):
                raise ValueError('Invalid video message')
            kind = message.get('type')
            if kind == 'ack':
                window.ack(message.get('sequence'))
            elif kind == 'resync':
                client.reset()
                resync = True
            elif kind != 'ping':
                raise ValueError('Unknown video message')

    async def transmit():
        nonlocal muxer, configured, generation, origin, resync
        last_status = 0
        while authenticated(ws):
            await window.ready()
            try:
                frame = await asyncio.wait_for(client.next_frame(max_age),1)
            except asyncio.TimeoutError:
                await asyncio.wait_for(ws.send_json({'type':'waiting'}),.75)
                continue
            await window.ready(len(frame.data)+1024)
            if time.monotonic()-frame.created>max_age:
                client.reset()
                resync = True
                continue
            if resync or configured != (frame.generation,frame.codec):
                if not frame.keyframe:
                    client.reset()
                    continue
                if muxer:
                    muxer.close()
                    muxer = None
                generation += 1
                origin = frame.pts
                config = {'type':'config','protocol':'KVM1','format':mode,'codec':frame.codec,
                          'generation':generation,'window_frames':window.max_frames,
                          'max_frame_age_ms':round(max_age*1000)}
                if mode=='mp4':
                    muxer = FragmentMuxer(frame)
                    config['init'] = base64.b64encode(muxer.init).decode()
                await asyncio.wait_for(ws.send_json(config),.75)
                configured = (frame.generation,frame.codec)
                resync = False
            data = muxer.mux(frame) if muxer else frame.data
            if not data:
                continue  # MP4 emits the first fragment when its next sample arrives.
            sequence = window.sent(len(data)+HEADER.size)
            timestamp = max(0,round((frame.pts-origin)*1000000/90000))
            payload = HEADER.pack(b'KVM1',int(frame.keyframe),sequence,generation,timestamp)+data
            await asyncio.wait_for(ws.send_bytes(payload),.75)
            if time.monotonic()-last_status>1:
                await asyncio.wait_for(ws.send_json({'type':'stats','dropped':client.dropped,
                    'inflight':len(window.pending),'ack_ms':window.ack_ms}),.75)
                last_status = time.monotonic()

    tasks = []
    close_code = 1000
    try:
        await ws.accept()
        tasks = [asyncio.create_task(receive()),asyncio.create_task(transmit())]
        done,_ = await asyncio.wait(tasks,return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect,asyncio.TimeoutError):
        close_code = 1013
    except (ValueError,RuntimeError,av.FFmpegError):
        close_code = 1008
    finally:
        with CancelScope(shield=True):
            client.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            if muxer:
                with suppress(av.FFmpegError):
                    muxer.close()
            with suppress(RuntimeError,WebSocketDisconnect):
                await ws.close(code=close_code)
