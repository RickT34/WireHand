"""Forward platform H.264 packets over WebRTC without decoding/re-encoding them."""
import asyncio
from contextlib import suppress
from fractions import Fraction
import time
import logging
import uuid

import av

from kvm_mcp.media import EncodedHub
from aiortc import (MediaStreamTrack, RTCConfiguration, RTCPeerConnection,
                    RTCRtpSender, RTCSessionDescription)
from aiortc.mediastreams import MediaStreamError


class EncodedTrack(MediaStreamTrack):
    kind = 'video'

    def __init__(self, hub):
        super().__init__()
        self.hub = hub
        self.queue = asyncio.Queue(maxsize=4)
        self.wait_keyframe = True
        hub.tracks.add(self)

    def reset(self):
        while not self.queue.empty():
            self.queue.get_nowait()
        self.wait_keyframe = True

    def push(self, data, pts, keyframe):
        if self.queue.full():
            self.reset()
        if self.wait_keyframe:
            if not keyframe:
                return
            self.wait_keyframe = False
        self.queue.put_nowait((data, pts))

    async def recv(self):
        if self.readyState != 'live':
            raise MediaStreamError
        item = await self.queue.get()
        if item is None:
            raise MediaStreamError
        data, pts = item
        packet = av.Packet(data)
        packet.pts = packet.dts = pts
        packet.time_base = Fraction(1, 90000)
        return packet

    def stop(self):
        super().stop()
        self.hub.tracks.discard(self)
        self.reset()
        self.queue.put_nowait(None)


class RTCManager:
    def __init__(self, hub):
        self.hub = hub
        self.peers = {}

    async def offer(self, description, expires_at):
        if len(self.peers) >= 4:
            raise RuntimeError('最多同时连接四个视频查看者')
        peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        identity = uuid.uuid4().hex
        entry = {'peer': peer, 'track': None, 'watchdog': None}
        self.peers[identity] = entry
        logger=logging.getLogger('uvicorn.error')
        @peer.on('connectionstatechange')
        async def changed():
            logger.info('RTC %s connection=%s ice=%s',identity[:8],peer.connectionState,peer.iceConnectionState)
        try:
            track = EncodedTrack(self.hub)
            entry['track'] = track
            transceiver = peer.addTransceiver(track, direction='sendonly')
            codecs = [c for c in RTCRtpSender.getCapabilities('video').codecs
                      if c.mimeType.lower() == 'video/h264']
            # aiortc chooses common codecs in setRemoteDescription, so this must
            # precede it. Otherwise a VP8-first offer mislabels our H.264 packets.
            transceiver.setCodecPreferences(codecs)
            await peer.setRemoteDescription(RTCSessionDescription(sdp=description, type='offer'))
            answer = await peer.createAnswer()
            if 'H264/90000' not in answer.sdp:
                raise ValueError('浏览器没有提供兼容的 H.264 编解码器')
            await asyncio.wait_for(peer.setLocalDescription(answer), 8)
            entry['watchdog'] = asyncio.create_task(self._watch(identity, expires_at))
            return {'id': identity, 'sdp': peer.localDescription.sdp, 'type': 'answer'}
        except BaseException:
            await self.close(identity)
            raise

    async def _watch(self, identity, expires_at):
        entry = self.peers[identity]
        started = time.monotonic()
        disconnected = None
        try:
            while True:
                await asyncio.sleep(1)
                state = entry['peer'].connectionState
                if time.time() >= expires_at or state in ('failed', 'closed'):
                    break
                if state != 'connected':
                    disconnected = disconnected or time.monotonic()
                    if time.monotonic() - disconnected > 15:
                        break
                else:
                    disconnected = None
                if time.monotonic() - started > 43200:
                    break
        finally:
            await self.close(identity)

    async def close(self, identity):
        entry = self.peers.pop(identity, None)
        if not entry:
            return
        task = entry['watchdog']
        if task and task is not asyncio.current_task():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if entry['track']:
            entry['track'].stop()
        await entry['peer'].close()

    async def close_all(self):
        await asyncio.gather(*(self.close(identity) for identity in tuple(self.peers)))
