import asyncio
import io
import time
import unittest
from dataclasses import replace
from fractions import Fraction

import av
from PIL import Image,ImageDraw

from kvm_automation.media import EncodedHub
from kvm_mcp.app.h264 import H264Relay, SendWindow, FragmentMuxer, EncodedFrame, nal_units


def encoded_fixture(count=35):
    codec=av.CodecContext.create('libx264','w')
    codec.width,codec.height=320,192
    codec.pix_fmt='yuv420p';codec.time_base=Fraction(1,60);codec.framerate=Fraction(60,1)
    codec.options={'preset':'ultrafast','tune':'zerolatency','profile':'baseline',
                   'x264-params':'keyint=30:min-keyint=30:scenecut=0:repeat-headers=1'}
    packets=[]
    for index in range(count):
        image=Image.new('RGB',(320,192),'#18343a')
        ImageDraw.Draw(image).rectangle((index*3,30,index*3+40,70),fill='orange')
        frame=av.VideoFrame.from_image(image);frame.pts=index
        packets.extend(codec.encode(frame))
    packets.extend(codec.encode(None))
    return [bytes(p) for p in packets]


class RelayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hub=EncodedHub();self.relay=H264Relay(self.hub)
        self.addCleanup(self.relay.close)
        self.key=b'\x00\x00\x00\x01\x67\x42\xc0\x20\x80\x00\x00\x01\x68\x11\x00\x00\x01\x65\x88'
        self.delta=b'\x00\x00\x01\x41\x99'

    async def test_late_subscriber_gets_codec_parameters_with_its_idr(self):
        self.hub.publish(self.key,0,True)
        client=self.relay.subscribe()
        self.hub.publish(self.delta,1500,False)
        self.assertTrue(client.queue.empty())
        self.hub.publish(b'\x00\x00\x01\x65\xaa',3000,True)
        packet=await client.next_frame()
        self.assertEqual([n[0]&31 for n in nal_units(packet.data)],[7,8,5])
        self.assertEqual(packet.codec,'avc1.42c020')

    async def test_overflow_discards_broken_prediction_chain(self):
        client=self.relay.subscribe()
        self.hub.publish(self.key,0,True)
        self.hub.publish(self.delta,1500,False)
        self.hub.publish(self.delta,3000,False)
        self.assertTrue(client.queue.empty())
        self.hub.publish(self.delta,4500,False)
        self.assertTrue(client.queue.empty())
        self.hub.publish(self.key,6000,True)
        self.assertTrue((await client.next_frame()).keyframe)

    async def test_stale_unsent_frame_waits_for_new_idr(self):
        client=self.relay.subscribe();self.hub.publish(self.key,0,True)
        old=client.queue.get_nowait();client.queue.put_nowait(replace(old,created=time.monotonic()-1))
        pending=asyncio.create_task(client.next_frame())
        await asyncio.sleep(0)
        self.assertFalse(pending.done())
        self.hub.publish(self.delta,1500,False)
        self.hub.publish(self.key,3000,True)
        self.assertEqual((await asyncio.wait_for(pending,1)).pts,3000)

    async def test_restart_clears_old_codec_and_notifies_subscribers(self):
        client=self.relay.subscribe();self.hub.publish(self.key,0,True)
        generation=self.relay.generation
        self.hub.reset()
        self.assertTrue(client.queue.empty())
        self.assertGreater(self.relay.generation,generation)
        self.hub.publish(b'\x00\x00\x01\x65\xaa',1500,True)
        self.assertTrue(client.queue.empty())

    async def test_window_blocks_until_ack_without_ignoring_byte_budget(self):
        window=SendWindow(max_frames=2,max_bytes=100)
        sequence=window.sent(80)
        waiting=asyncio.create_task(window.ready(30));await asyncio.sleep(0)
        self.assertFalse(waiting.done())
        window.ack(sequence);await asyncio.wait_for(waiting,1)
        with self.assertRaises(ValueError):window.ack(sequence+100)

    async def test_missing_ack_times_out(self):
        window=SendWindow(max_frames=1,ack_timeout=.02)
        window.sent(10)
        with self.assertRaises(asyncio.TimeoutError):await window.ready()


class CodecTests(unittest.TestCase):
    def test_mp4_remux_preserves_decodable_frames_and_dimensions(self):
        packets=encoded_fixture(10)
        frames=[EncodedFrame(data,i*1500,any(n[0]&31==5 for n in nal_units(data)),1,'avc1.42c020',time.monotonic())
                for i,data in enumerate(packets)]
        muxer=FragmentMuxer(frames[0]);parts=[muxer.init]
        try:
            parts.extend(muxer.mux(frame) for frame in frames)
        finally:parts.append(muxer.close())
        with av.open(io.BytesIO(b''.join(parts)),format='mp4') as container:
            decoded=list(container.decode(video=0))
        self.assertEqual(len(decoded),10)
        self.assertEqual((decoded[0].width,decoded[0].height),(320,192))

    def test_annexb_relay_output_decodes_after_late_join(self):
        hub=EncodedHub();relay=H264Relay(hub)
        try:
            packets=encoded_fixture()
            hub.publish(packets[0],0,True)
            client=relay.subscribe()
            for i,data in enumerate(packets[1:],1):
                # Simulate an encoder sending SPS/PPS only at startup.
                units=[n for n in nal_units(data) if n[0]&31 not in (7,8)]
                hub.publish(b''.join(b'\x00\x00\x00\x01'+n for n in units),i*1500,False)
                if not client.queue.empty():break
            frame=client.queue.get_nowait()
            codec=av.CodecContext.create('h264','r')
            decoded=codec.decode(av.Packet(frame.data))
            self.assertTrue(decoded)
            self.assertEqual(decoded[0].width,320)
        finally:relay.close()

class WebSocketTests(unittest.TestCase):
    def app(self, runtime, **options):
        from tests.fakes import FakeBackend
        from kvm_mcp.app.web import make_app
        from kvm_automation.ownership import InputGate
        from pathlib import Path
        packets=encoded_fixture()
        class Video:
            encoded=EncodedHub()
            def status(self):return {'signal':True,'error':'','width':320,'height':192}
            async def next_frame(self,after):raise NotImplementedError
            async def run(self):
                index=0
                while True:
                    for data in packets:
                        self.encoded.publish(data,index*1500,False);index+=1
                        await asyncio.sleep(.005)
                    self.encoded.reset()
        backend=FakeBackend();backend.video=Video()
        return make_app('test-secret-more-than-twenty',backend,InputGate(Path(runtime)),
                        hosts=['testserver','kvm.example.test'],**options)

    def login(self,client,headers=None,url='/login'):
        response=client.post(url,data={'token':'test-secret-more-than-twenty'},
                             headers=headers or {'Origin':'http://testserver'},follow_redirects=False)
        self.assertEqual(response.status_code,303)
        return response

    def test_authenticated_socket_delivers_decodable_idr_and_releases_subscription(self):
        import tempfile,json
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        from kvm_mcp.app.h264 import HEADER
        with tempfile.TemporaryDirectory() as directory:
            app=self.app(directory)
            with TestClient(app) as client:
                self.login(client)
                decoder=av.CodecContext.create('h264','r');decoded=[]
                with client.websocket_connect('/ws/h264',headers={'Origin':'http://testserver'}) as ws:
                    for _ in range(15):
                        message=ws.receive()
                        if 'text' in message:continue
                        data=message['bytes'];magic,key,sequence,generation,pts=HEADER.unpack(data[:HEADER.size])
                        self.assertEqual(magic,b'KVM1')
                        decoded.extend(decoder.decode(av.Packet(data[HEADER.size:])))
                        ws.send_json({'type':'ack','sequence':sequence})
                        if decoded:break
                    self.assertTrue(decoded)
                    self.assertEqual(decoded[0].width,320)
                    ws.close()
                    for _ in range(20):
                        if ws.receive()['type']=='websocket.close':break
                    else:self.fail('Video connection did not close')

    def test_anonymous_and_cross_origin_sockets_are_rejected(self):
        import tempfile
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(self.app(directory)) as client:
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect('/ws/h264',headers={'Origin':'http://testserver'}):pass
                self.login(client)
                with self.assertRaises(WebSocketDisconnect):
                    with client.websocket_connect('/ws/h264',headers={'Origin':'https://evil.example'}):pass

    def test_future_ack_closes_connection(self):
        import tempfile
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(self.app(directory)) as client:
                self.login(client)
                with client.websocket_connect('/ws/h264',headers={'Origin':'http://testserver'}) as ws:
                    ws.send_json({'type':'ack','sequence':999999})
                    message=ws.receive()
                    self.assertEqual(message['type'],'websocket.close')
                    self.assertEqual(message['code'],1008)

    def test_trusted_ssl_proxy_preserves_origin_secure_cookie_and_websocket(self):
        import tempfile
        from starlette.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory:
            app=self.app(directory,trusted_proxies=['127.0.0.1'])
            with TestClient(app,base_url='http://kvm.example.test',client=('127.0.0.1',1234)) as client:
                headers={'Origin':'https://kvm.example.test','X-Forwarded-Proto':'https'}
                response=self.login(client,headers)
                self.assertIn('Secure',response.headers['set-cookie'])
                headers['Cookie']='; '.join(f'{key}={value}' for key,value in client.cookies.items())
                with client.websocket_connect('ws://kvm.example.test/ws/h264',headers=headers) as ws:
                    message=ws.receive_json()
                    self.assertEqual(message['type'],'config')

    def test_untrusted_forwarded_proto_cannot_bypass_origin_validation(self):
        import tempfile
        from starlette.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory:
            app=self.app(directory,trusted_proxies=['127.0.0.1'])
            with TestClient(app,client=('203.0.113.10',4321)) as client:
                response=client.post('/login',data={'token':'test-secret-more-than-twenty'},
                    headers={'Origin':'https://testserver','X-Forwarded-Proto':'https'})
                self.assertEqual(response.status_code,403)
            with self.assertRaises(ValueError):self.app(directory,trusted_proxies=['*'])
