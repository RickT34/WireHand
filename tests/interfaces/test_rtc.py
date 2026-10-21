import asyncio
from fractions import Fraction
import unittest
import time

import av
from aiortc.codecs.h264 import H264Encoder
from aiortc import RTCPeerConnection, RTCConfiguration
from aiortc.mediastreams import MediaStreamError

from kvm.core.media import EncodedHub
from kvm.interfaces.web.video.rtc import EncodedTrack, RTCManager


class PacketTests(unittest.IsolatedAsyncioTestCase):
    async def test_platform_bytes_reach_webrtc_without_codec_objects_in_contract(self):
        hub = EncodedHub()
        track = EncodedTrack(hub)
        self.addCleanup(track.stop)
        hub.publish(b'delta', 90000, False)
        hub.publish(b'keyframe', 180000, True)
        packet = await track.recv()
        self.assertEqual(bytes(packet), b'keyframe')
        self.assertEqual(packet.pts, 180000)

    async def test_vp8_first_offer_negotiates_only_h264(self):
        client = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        manager = RTCManager(EncodedHub())
        try:
            client.addTransceiver('video',direction='recvonly')
            offer = await client.createOffer()
            answer = await manager.offer(offer.sdp,time.time()+60)
            self.assertIn('H264/90000',answer['sdp'])
            self.assertNotIn('VP8/90000',answer['sdp'])
        finally:
            await manager.close_all()
            await client.close()

    async def test_new_viewer_waits_for_decodable_keyframe(self):
        track = EncodedTrack(EncodedHub())
        self.addCleanup(track.stop)
        track.push(b'delta', 100, False)
        track.push(b'keyframe', 200, True)
        packet = await track.recv()
        self.assertEqual(bytes(packet), b'keyframe')
        self.assertEqual(packet.pts, 200)
        self.assertEqual(packet.time_base, Fraction(1,90000))

    async def test_slow_viewer_drops_backlog_and_waits_for_next_keyframe(self):
        track = EncodedTrack(EncodedHub())
        self.addCleanup(track.stop)
        track.push(b'key0', 0, True)
        track.push(b'delta1', 1, False)
        track.push(b'delta2', 2, False)
        track.push(b'delta3', 3, False)
        track.push(b'overflow', 4, False)
        self.assertTrue(track.queue.empty())
        track.push(b'key5', 5, True)
        self.assertEqual(bytes(await track.recv()), b'key5')

    async def test_stopping_track_unblocks_receiver(self):
        track = EncodedTrack(EncodedHub())
        waiting = asyncio.create_task(track.recv())
        await asyncio.sleep(0)
        track.stop()
        with self.assertRaises(MediaStreamError):
            await asyncio.wait_for(waiting, .5)

    async def test_encoder_restart_discards_previous_delta_frames(self):
        hub = EncodedHub()
        track = EncodedTrack(hub)
        self.addCleanup(track.stop)
        track.push(b'oldkey', 0, True)
        hub.reset()
        track.push(b'old-delta', 1, False)
        track.push(b'new-key', 2, True)
        self.assertEqual(bytes(await track.recv()), b'new-key')

    def test_packetizer_does_not_create_software_encoder(self):
        # An Annex-B NAL can be packetized directly; encode() must never run.
        packet = av.Packet(b'\x00\x00\x00\x01\x65\x88\x84')
        packet.pts = 90000
        packet.time_base = Fraction(1,90000)
        encoder = H264Encoder()
        payloads, timestamp = encoder.pack(packet)
        self.assertEqual(payloads, [b'\x65\x88\x84'])
        self.assertEqual(timestamp, 90000)
        self.assertIsNone(encoder.codec)

    async def test_invalid_sdp_does_not_leak_a_peer(self):
        manager = RTCManager(EncodedHub())
        with self.assertRaises((ValueError, AssertionError)):
            await manager.offer('not SDP', 9999999999)
        self.assertFalse(manager.peers)



if __name__ == '__main__':
    unittest.main()
