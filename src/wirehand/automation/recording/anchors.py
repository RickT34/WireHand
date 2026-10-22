"""Capture local click targets and persist their image assets."""
from pathlib import Path

from wirehand.vision.detection import ContourDetector
from wirehand.vision.rendering import padded


class AnchorStore:
    def __init__(self, directory, targets, detector=None):
        self.directory = Path(directory)
        self.targets = targets
        self.detector = detector or ContourDetector()
        self.bytes_saved = 0

    def capture(self, frame, point):
        """Save the nearest clicked element without requiring unique self-matching."""
        width,height = frame.size
        x,y = point
        boxes = [c.box for c in self.detector.detect(frame)
                 if c.box[0]-8<=x<c.box[2]+8 and c.box[1]-8<=y<c.box[3]+8
                 and (c.box[2]-c.box[0])*(c.box[3]-c.box[1]) < width*height/2]
        box = min(boxes,key=lambda b:(b[2]-b[0])*(b[3]-b[1])) if boxes else (int(x)-24,int(y)-20,int(x)+25,int(y)+21)
        box = (min(box[0],int(x)),min(box[1],int(y)),max(box[2],int(x)+1),max(box[3],int(y)+1))
        bounds = padded(box,frame.size,12)
        identity = f'target_{len(self.targets)+1:04}'
        path = self.directory/'assets'/f'{identity}.png'
        frame.crop(bounds).save(path)
        path.chmod(0o600)
        self.bytes_saved += path.stat().st_size
        if self.bytes_saved>64*1024*1024:
            raise ValueError('Recording reached the 64 MiB image limit')
        self.targets[identity] = {'image':f'assets/{identity}.png','point':[x-bounds[0],y-bounds[1]],
                                 'screen_size':list(frame.size),'box':list(bounds),
                                 'strategy':'element' if boxes else 'patch'}
        return identity
