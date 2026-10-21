"""Offline demonstration: record a button, then replay after moving that button.

No OS input is sent. Uses kvm.automation without starting MCP or a web server.
"""
import argparse
from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile

from PIL import Image,ImageDraw,ImageFont
from kvm.automation import Computer,Recording


class DemoBackend:
    name='demo'
    def __init__(self, origin=(50,70)):
        self.origin=origin
        self.events=[]
    def screenshot(self,max_width=3840):
        image=Image.new('RGB',(480,320),'#263440')
        draw=ImageDraw.Draw(image)
        draw.text((12,12),'Automation demo',fill='white',font=ImageFont.load_default(size=16))
        x,y=self.origin
        draw.rounded_rectangle((x,y,x+110,y+44),radius=7,fill='#417cc2',outline='white',width=2)
        draw.text((x+18,y+10),'OPEN',fill='white',font=ImageFont.load_default(size=21))
        out=io.BytesIO();image.save(out,'JPEG',quality=98);return out.getvalue()
    @contextmanager
    def open_input(self):
        backend=self
        class Input:
            def click(self,x,y,button='none',wheel=0):backend.events.append({'action':'click','point':[x*479,y*319],'button':button})
            def type_text(self,text):backend.events.append({'action':'write','text':text})
            def keys(self,keys):backend.events.append({'action':'keys','keys':keys})
            def pointer(self,x,y,buttons=0,wheel=0):backend.events.append({'action':'pointer','point':[x*479,y*319],'buttons':buttons,'wheel':wheel})
            def press_key(self,keys,hold_ms=50):backend.events.append({'action':'hotkey','keys':keys})
            def release_all(self):pass
        yield Input()
    def input_state(self):return {'connected':True,'detail':'Simulation only'}
    def status(self):return {'platform':self.name,'input':self.input_state(),'video':{'signal':True,'error':''}}
    def create_video(self):raise RuntimeError('No video transport in this demo')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='kvm-demo-') as runtime:
        original=DemoBackend()
        computer=Computer(original,Path(runtime)/'record')
        with computer.record(args.output):
            computer.click(100,90)
            computer.write('hello')
        moved=DemoBackend((240,170))
        Recording.load(args.output).replay(Computer(moved,Path(runtime)/'replay'))
        point=moved.events[0]['point']
        assert abs(point[0]-290)<3 and abs(point[1]-190)<3, moved.events
        computer.screenshot().save(args.output/'before.png')
        Computer(moved,Path(runtime)/'read').screenshot().save(args.output/'after.png')
        result={'recording':str(args.output.resolve()),'recorded_point':[100,90],
                'replayed_events':moved.events,'physical_input_sent':False}
        (args.output/'result.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))


if __name__=='__main__':main()
