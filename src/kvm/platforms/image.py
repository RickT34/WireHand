"""Read-only image backend for development without capture hardware."""
import io
import os
from pathlib import Path

from PIL import Image


class ImageBackend:
    """Read-only replay of an image for the observation pipeline."""
    name = 'saved-screen'

    def __init__(self, path=None, *, runtime=None):
        self.path = Path(path or os.environ['KVM_SCREENSHOT'])
        self.data = self.path.read_bytes()

    def screenshot(self, max_width=3840):
        with Image.open(io.BytesIO(self.data)) as source:
            if source.format == 'JPEG' and source.width <= max_width:
                return self.data
            image = source.convert('RGB')
            if image.width > max_width:
                image.thumbnail((max_width,max(1,round(image.height*max_width/image.width))))
            output = io.BytesIO()
            image.save(output,'JPEG',quality=95)
            return output.getvalue()

    def input_state(self):
        return {'connected':False,'detail':'Offline image'}

    def status(self):
        return {'platform':self.name,'input':self.input_state(),'video':{'signal':True,'error':''}}

    def open_input(self):
        raise RuntimeError('Saved screenshots do not accept input')

    def create_video(self):
        raise RuntimeError('Use kvm-inspect for saved screenshots')
