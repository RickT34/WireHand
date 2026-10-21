"""Choose distinctive visual anchors and persist their image assets."""
from pathlib import Path

from kvm.vision.matching import VisualTarget, TargetNotFound, locate
from kvm.vision.detection import ContourDetector
from kvm.vision.rendering import padded


class AnchorStore:
    def __init__(self, directory, targets, detector=None):
        self.directory = Path(directory)
        self.targets = targets
        self.detector = detector or ContourDetector()
        self.bytes_saved = 0

    def capture(self, frame, point, *, scene=False):
        width,height = frame.size
        x,y = point
        boxes = [] if scene else [c.box for c in self.detector.detect(frame)
                                   if c.box[0]-8<=x<c.box[2]+8 and c.box[1]-8<=y<c.box[3]+8]
        box = min(boxes,key=lambda b:(b[2]-b[0])*(b[3]-b[1])) if boxes else (int(x)-24,int(y)-20,int(x)+25,int(y)+21)
        box = (min(box[0],int(x)),min(box[1],int(y)),max(box[2],int(x)+1),max(box[3],int(y)+1))
        identity = f'target_{len(self.targets)+1:04}'
        path = self.directory/'assets'/f'{identity}.png'
        strategy = 'element' if boxes else 'patch'
        options = [(0,0,width,height)] if scene else [padded(box,frame.size,p) for p in (12,32,64,128)] + [(0,0,width,height)]
        for bounds in options:
            frame.crop(bounds).save(path)
            offset = (x-bounds[0],y-bounds[1])
            target = VisualTarget(path,offset,frame.size)
            try:
                match = locate(frame,target)
                if max(abs(match.point[0]-x),abs(match.point[1]-y))<=3:
                    break
            except TargetNotFound:
                continue
        else:
            raise ValueError('No distinctive visual anchor at this input; recording cannot be replayed reliably')
        if bounds==(0,0,width,height):strategy='screen'
        path.chmod(0o600)
        self.bytes_saved += path.stat().st_size
        if self.bytes_saved>64*1024*1024:
            raise ValueError('Recording reached the 64 MiB image limit')
        self.targets[identity] = {'image':f'assets/{identity}.png','point':list(offset),
                                         'screen_size':list(frame.size),'box':list(bounds),
                                         'strategy':strategy}
        return identity
