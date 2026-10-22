"""Bounded screenshot observations and guarded ID-to-input resolution."""
from collections import OrderedDict
from dataclasses import dataclass, field
import threading
import time
import uuid

from PIL import Image

from wirehand.vision.images import decode, changed_fraction
from wirehand.core.contracts import Backend
from wirehand.core.ownership import InputGate
from wirehand.vision.detection import ContourDetector, overlap
from wirehand.vision.models import Candidate, Detector, Target, View
from wirehand.vision.rendering import extent, padded, partition, png, render_overview, render_view


@dataclass
class Snapshot:
    id: str
    image: Image.Image
    created: float
    revision: str
    targets: dict[str, Target] = field(default_factory=dict)
    views: dict[str, View] = field(default_factory=dict)
    roots: list[str] = field(default_factory=list)
    coarse: list[str] = field(default_factory=list)
    refinements: dict[tuple[str, str], list[str]] = field(default_factory=dict)


class StaleSnapshot(RuntimeError):
    pass


@dataclass
class Inspection:
    metadata: dict
    images: list[bytes]


def validate_frame(original, current, box):
    if original.size != current.size:
        raise StaleSnapshot('Screen dimensions changed; capture a new inspection')
    size = (480, max(1, round(original.height*480/original.width)))
    scene = changed_fraction(original.resize(size), current.resize(size))
    local_box = padded(box, original.size, 18)
    local = changed_fraction(original.crop(local_box), current.crop(local_box))
    if scene > .04 or local > .08:
        raise StaleSnapshot('Screen or target changed; capture a new inspection')
    return {'scene_changed_fraction': round(scene,4), 'target_changed_fraction': round(local,4)}


class Inspector:
    def __init__(self, backend: Backend, gate: InputGate, detector: Detector | None = None,
                 *, ttl=120, max_snapshots=4, per_view=18):
        self.backend, self.gate = backend, gate
        self.detector = detector or ContourDetector()
        self.ttl, self.max_snapshots, self.per_view = ttl, max_snapshots, per_view
        self.snapshots: OrderedDict[str, Snapshot] = OrderedDict()
        self.mutex = threading.RLock()

    def _get(self, identity):
        snapshot = self.snapshots.get(identity)
        if snapshot is None:
            raise StaleSnapshot('Unknown or evicted snapshot; capture a new inspection')
        if time.monotonic()-snapshot.created > self.ttl:
            del self.snapshots[identity]
            raise StaleSnapshot('Snapshot expired; capture a new inspection')
        if snapshot.revision != self.gate.revision:
            raise StaleSnapshot('Input/control ownership changed; capture a new inspection')
        return snapshot

    def _targets(self, snapshot, candidates):
        result = []
        for candidate in candidates:
            x1,y1,x2,y2 = candidate.box
            if not (0 <= x1 < x2 <= snapshot.image.width and 0 <= y1 < y2 <= snapshot.image.height):
                raise ValueError('Detector returned a box outside the screenshot')
            existing = next((t for t in snapshot.targets.values()
                             if t.candidate.kind == candidate.kind and overlap(candidate.box,t.candidate.box)[1] > .85), None)
            if existing is None:
                if len(snapshot.targets) >= 1200:
                    raise ValueError('Too many visual proposals; use a less noisy detector')
                identity = f'T{len(snapshot.targets)+1:03}'
                existing = Target(identity, candidate)
                snapshot.targets[identity] = existing
            if existing not in result:
                result.append(existing)
        return result

    def _views(self, snapshot, targets):
        identities = []
        for group in partition(targets, self.per_view):
            if len(snapshot.views) >= 160:
                raise ValueError('Too many refinements; capture a new inspection')
            identity = f'R{len(snapshot.views)+1:02}'
            snapshot.views[identity] = View(identity, padded(extent(group),snapshot.image.size),group)
            identities.append(identity)
        return identities

    def _bundle(self, snapshot, ids, page, page_size):
        if not 1 <= page_size <= 4:
            raise ValueError('page_size must be 1..4 (plus one overview image)')
        count = max(1,(len(ids)+page_size-1)//page_size)
        if not 0 <= page < count:
            raise ValueError(f'page must be 0..{count-1}')
        selected = ids[page*page_size:(page+1)*page_size]
        views = [snapshot.views[i] for i in selected]
        # Overview shows only this page's region boxes, avoiding a new label pileup.
        images = [png(render_overview(snapshot.image,views))]
        regions = [{'id':i,'box':snapshot.views[i].box,'count':len(snapshot.views[i].targets)} for i in ids]
        targets = []
        for view in views:
            images.append(png(render_view(snapshot.image,view)))
            for target in sorted(view.targets,key=lambda t:(t.candidate.center[1]//16,t.candidate.center[0])):
                cx,cy = target.candidate.center
                targets.append({'id':target.id,'region':view.id,'kind':target.candidate.kind,
                                'box':target.candidate.box,'point':[cx,cy],
                                'text':target.candidate.text})
        metadata = {'snapshot_id':snapshot.id,'size':list(snapshot.image.size),'page':page,
                    'presentation':'targets','total_candidates':len(snapshot.targets),
                    'pages':count,'next_page':page+1 if page+1<count else None,
                    'expires_in_seconds':max(0,round(self.ttl-(time.monotonic()-snapshot.created))),
                    'regions':regions,'targets':targets,'image_regions':['overview',*selected]}
        return Inspection(metadata,images)

    def _overview(self, snapshot, page, page_size):
        if page != 0 or not 1 <= page_size <= 4:
            raise ValueError('Dense overview has one page; inspect a region to see its targets')
        views = [snapshot.views[identity] for identity in snapshot.coarse]
        metadata = {'snapshot_id':snapshot.id,'size':list(snapshot.image.size),
                    'presentation':'regions','total_candidates':sum(len(snapshot.views[i].targets) for i in snapshot.roots),
                    'page':0,'pages':1,'next_page':None,
                    'expires_in_seconds':max(0,round(self.ttl-(time.monotonic()-snapshot.created))),
                    'regions':[{'id':v.id,'box':v.box,'count':len(v.targets)} for v in views],
                    'targets':[],'image_regions':['overview']}
        return Inspection(metadata,[png(render_overview(snapshot.image,views))])

    def inspect(self, snapshot_id='', page=0, page_size=3):
        with self.mutex:
            if snapshot_id:
                snapshot = self._get(snapshot_id)
            else:
                if page != 0:
                    raise ValueError('New captures start at page 0; pass snapshot_id to paginate')
                if not 1 <= page_size <= 4:
                    raise ValueError('page_size must be 1..4')
                revision = self.gate.revision
                image = decode(self.backend.screenshot(3840))
                snapshot = Snapshot(uuid.uuid4().hex[:12],image,time.monotonic(),revision)
                if revision != self.gate.revision:
                    raise StaleSnapshot('Input changed during capture; capture a new inspection')
                targets = self._targets(snapshot,self.detector.detect(image))
                snapshot.roots = self._views(snapshot,targets)
                if len(targets)>180:
                    for row in range(3):
                        for col in range(3):
                            box = (image.width*col//3,image.height*row//3,
                                   image.width*(col+1)//3,image.height*(row+1)//3)
                            identity = f'C{row*3+col+1:02}'
                            group = [t for t in targets if box[0]<=t.candidate.center[0]<box[2]
                                     and box[1]<=t.candidate.center[1]<box[3]]
                            snapshot.views[identity] = View(identity,box,group)
                            snapshot.coarse.append(identity)
                # Discard expired frames as well as least-recently-created captures.
                self.snapshots = OrderedDict((k,s) for k,s in self.snapshots.items()
                                             if time.monotonic()-s.created <= self.ttl)
                self.snapshots[snapshot.id] = snapshot
                while len(self.snapshots)>self.max_snapshots:
                    self.snapshots.popitem(last=False)
            if snapshot.coarse:
                return self._overview(snapshot,page,page_size)
            return self._bundle(snapshot,snapshot.roots,page,page_size)

    def region(self, snapshot_id, region_id, mode='detect', page=0, page_size=3):
        with self.mutex:
            snapshot = self._get(snapshot_id)
            if mode not in ('detect','grid'):
                raise ValueError('mode must be detect or grid')
            if region_id == 'FULL':
                box = (0,0,*snapshot.image.size)
            elif region_id in snapshot.views:
                box = snapshot.views[region_id].box
            elif region_id in snapshot.targets:
                box = snapshot.targets[region_id].candidate.box
            else:
                raise ValueError('Unknown region/target ID')
            key = (region_id,mode)
            if key not in snapshot.refinements:
                x1,y1,x2,y2 = box
                if x2-x1 < 4 or y2-y1 < 4:
                    raise ValueError('Region too small to refine')
                if mode == 'grid':
                    candidates = [Candidate((x1+(x2-x1)*col//4,y1+(y2-y1)*row//4,
                                              x1+(x2-x1)*(col+1)//4,y1+(y2-y1)*(row+1)//4),kind='grid')
                                  for row in range(4) for col in range(4)]
                else:
                    candidates = [t.candidate for t in snapshot.targets.values()
                                  if t.candidate.kind!='grid' and x1<=t.candidate.center[0]<x2
                                  and y1<=t.candidate.center[1]<y2]
                    candidates += [Candidate((c.box[0]+x1,c.box[1]+y1,c.box[2]+x1,c.box[3]+y1),c.kind,c.text)
                                  for c in self.detector.detect(snapshot.image.crop(box))]
                targets = self._targets(snapshot,candidates)
                snapshot.refinements[key] = self._views(snapshot,targets)
            return self._bundle(snapshot,snapshot.refinements[key],page,page_size)

    def click(self, snapshot_id, target_id, button='left'):
        if button not in ('left','right','middle'):
            raise ValueError('button must be left, right or middle')
        with self.mutex, self.gate.automated():
            snapshot = self._get(snapshot_id)
            target = snapshot.targets.get(target_id)
            if target is None:
                raise ValueError('Unknown target ID')
            current = decode(self.backend.screenshot(3840))
            verification = validate_frame(snapshot.image,current,target.candidate.box)
            self._get(snapshot_id)  # Capture may have blocked past the expiry time.
            cx,cy = target.candidate.center
            x,y = cx/(snapshot.image.width-1),cy/(snapshot.image.height-1)
            try:
                with self.backend.open_input() as session:
                    session.click(x,y,button,0)
            finally:
                self.snapshots.clear()
            return {'clicked':target_id,'snapshot_id':snapshot_id,'point':[cx,cy],
                    'normalized':[x,y],'verification':verification}
