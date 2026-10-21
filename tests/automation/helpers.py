from contextlib import contextmanager
import io
from PIL import Image,ImageDraw,ImageFont
from tests.fakes import FakeBackend,FakeInput


def screen(origin=(50,70), duplicate=False):
    image=Image.new('RGB',(480,320),'#263440')
    draw=ImageDraw.Draw(image)
    draw.text((10,8),'A demo application',font=ImageFont.load_default(size=16),fill='white')
    positions=[origin]+([(280,70)] if duplicate else [])
    for x,y in positions:
        draw.rounded_rectangle((x,y,x+110,y+44),radius=7,fill='#417cc2',outline='white',width=2)
        draw.text((x+18,y+10),'OPEN',font=ImageFont.load_default(size=21),fill='white')
    return image


class ScreenBackend(FakeBackend):
    def __init__(self,image=None):
        super().__init__()
        self.image=image if image is not None else screen()
        self.frames=[]
        self.captures=0

    def screenshot(self,max_width=3840):
        self.captures+=1
        image=self.frames.pop(0) if self.frames else self.image
        out=io.BytesIO();image.save(out,'PNG');return out.getvalue()
