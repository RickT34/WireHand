"""Local H.264 transport demo, using a screenshot and software encoding only.

uv run python examples/video_transport_demo.py --image screenshot.jpg
The password is written to --token-file; no HID device is opened.
"""
import argparse
import asyncio
from fractions import Fraction
import io
from pathlib import Path
import secrets
import tempfile
import time

import av
from PIL import Image,ImageDraw
import uvicorn

from wirehand.core.media import EncodedHub
from wirehand.core.ownership import InputGate
from wirehand.interfaces.web.app import make_app


class DemoVideo:
    def __init__(self, image, fps=60):
        self.image=image
        self.fps=fps
        self.encoded=EncodedHub()
        self.frame=None
        self.sequence=0
        self.condition=asyncio.Condition()

    def status(self):
        return {'signal':self.sequence>0,'error':'','width':self.image.width,'height':self.image.height,
                'fps':self.fps,'codec':'H264','test_source':True,'transport':'h264'}

    def encode(self,index,codec):
        image=self.image.copy()
        draw=ImageDraw.Draw(image)
        x=(index*8)%(image.width-80)
        draw.rectangle((x,image.height-42,x+75,image.height-4),fill='#00ff88')
        draw.text((12,12),f'LOCAL TEST  frame={index}',fill='white',stroke_width=2,stroke_fill='black')
        frame=av.VideoFrame.from_image(image)
        frame.pts=index
        packets=[(bytes(p),p.is_keyframe) for p in codec.encode(frame)]
        jpeg=None
        if index % max(1,self.fps//5)==0:
            output=io.BytesIO();image.save(output,'JPEG',quality=70);jpeg=output.getvalue()
        return packets,jpeg

    async def run(self):
        codec=av.CodecContext.create('libx264','w')
        codec.width,codec.height=self.image.size
        codec.pix_fmt='yuv420p';codec.time_base=Fraction(1,self.fps);codec.framerate=Fraction(self.fps,1)
        codec.bit_rate=2500000
        codec.options={'preset':'ultrafast','tune':'zerolatency','profile':'baseline',
                       'x264-params':f'keyint={self.fps//2}:min-keyint={self.fps//2}:scenecut=0:repeat-headers=1:vbv-maxrate=3300:vbv-bufsize=400'}
        index=0;started=time.monotonic()
        try:
            while True:
                packets,jpeg=await asyncio.to_thread(self.encode,index,codec)
                for data,keyframe in packets:self.encoded.publish(data,index*90000//self.fps,keyframe)
                if jpeg:
                    self.frame=jpeg;self.sequence+=1
                    async with self.condition:self.condition.notify_all()
                index+=1
                await asyncio.sleep(max(0,started+index/self.fps-time.monotonic()))
        finally:self.encoded.reset()

    async def next_frame(self,after):
        async with self.condition:
            await self.condition.wait_for(lambda:self.sequence>after and self.frame is not None)
        return self.sequence,self.frame


class DemoBackend:
    name='video-demo'
    def __init__(self,image,fps=60):self.video=DemoVideo(image,fps)
    def create_video(self):return self.video
    def input_state(self):return {'connected':False,'detail':'Local video demo; input disabled'}
    def status(self):return {'platform':self.name,'video':self.video.status(),'input':self.input_state()}
    def screenshot(self,max_width=1280):
        if self.video.frame:return self.video.frame
        output=io.BytesIO();self.video.image.save(output,'JPEG');return output.getvalue()
    def open_input(self):raise RuntimeError('The demo does not send physical input')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',type=Path)
    parser.add_argument('--port',type=int,default=8800)
    parser.add_argument('--fps',type=int,default=60)
    parser.add_argument('--token-file',type=Path,default=Path('/tmp/kvm-h264-demo-token'))
    parser.add_argument('--trusted-proxy',action='append',default=[])
    args=parser.parse_args()
    image=Image.open(args.image).convert('RGB') if args.image else Image.new('RGB',(1280,720),'#18343a')
    image.thumbnail((1280,720));image=image.resize((image.width//2*2,image.height//2*2))
    token=secrets.token_urlsafe(24)
    with args.token_file.open('w') as stream:
        args.token_file.chmod(0o600);stream.write(token)
    with tempfile.TemporaryDirectory(prefix='kvm-h264-demo-') as directory:
        app=make_app(token,DemoBackend(image,args.fps),InputGate(Path(directory)),trusted_proxies=args.trusted_proxy)
        print(f'Local demo: http://127.0.0.1:{args.port}/ ; password file: {args.token_file}',flush=True)
        uvicorn.run(app,host='127.0.0.1',port=args.port,proxy_headers=False,ws_max_size=4096,ws_max_queue=8,ws_per_message_deflate=False,access_log=False)

if __name__=='__main__':main()
