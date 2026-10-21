"""Synchronous, PyAutoGUI-style operations over an injected device backend."""
from contextlib import contextmanager
import math
from pathlib import Path
import threading
import time

from kvm.core.contracts import Backend
from kvm.core.keys import MODIFIERS
from kvm.core.ownership import InputGate
from kvm.vision.matching import Match, TargetNotFound, VisualTarget, locate
from kvm.vision.images import decode


class AutomationInterrupted(RuntimeError):
    pass


class Computer:
    def __init__(self, backend: Backend, runtime: Path, *, timeout=10., poll_interval=.15):
        if not all(math.isfinite(v) and v>0 for v in (timeout,poll_interval)):
            raise ValueError('timeout and poll_interval must be positive')
        self.backend = backend
        self.gate = InputGate(runtime)
        self.timeout, self.poll_interval = timeout,poll_interval
        self._revision = self.gate.revision
        self._cancelled = threading.Event()
        self._mutex = threading.RLock()
        self._recorder = None


    def __enter__(self):
        return self

    def __exit__(self,*exc):
        self.cancel()

    def cancel(self):
        self._cancelled.set()

    def _check(self):
        if self._cancelled.is_set():
            raise AutomationInterrupted('Automation cancelled')
        if self.gate.manual or self.gate.revision != self._revision:
            self._cancelled.set()
            raise AutomationInterrupted('Input ownership changed; start a fresh automation session')

    def _pause(self, seconds):
        if self._cancelled.wait(seconds):
            raise AutomationInterrupted('Automation cancelled')
        self._check()

    def screenshot(self):
        return decode(self.backend.screenshot(3840))

    def size(self):
        return self.screenshot().size

    def locate(self,target):
        return locate(self.screenshot(),target)

    @contextmanager
    def record(self, directory):
        from kvm.automation.recording import Recorder
        with self._mutex:
            if self._recorder is not None:raise RuntimeError('Recording already active')
            recorder = Recorder(self.backend,directory)
            self._recorder = recorder
        try:
            yield recorder
        except BaseException:
            recorder.finish(interrupted=True)
            raise
        else:
            recorder.finish()
        finally:
            self._recorder = None

    def wait_for(self, target: VisualTarget, timeout=None) -> Match:
        if timeout is not None and (not math.isfinite(timeout) or timeout<0):
            raise ValueError('timeout must be finite and non-negative')
        deadline = time.monotonic()+(self.timeout if timeout is None else timeout)
        previous = None
        error = TargetNotFound('Target not visible')
        while True:
            self._check()
            try:
                match = locate(self.screenshot(),target)
                if previous and max(abs(match.point[i]-previous.point[i]) for i in (0,1))<=2:
                    return match
                previous = match
            except TargetNotFound as exc:
                error,previous = exc,None
            if time.monotonic()>=deadline:
                raise type(error)(f'Timed out waiting for {target.image.name}: {error}')
            self._pause(min(self.poll_interval,max(0,deadline-time.monotonic())))

    @contextmanager
    def _input(self):
        self._check()
        lease = None
        try:
            with self.gate.automated() as lease:
                self._check()
                with self.backend.open_input() as session:
                    yield self._recorder.wrap(session) if self._recorder else session
        finally:
            # Only absorb revisions from our own completed lock scope, not a
            # human intervention that prevented lock acquisition.
            if lease is not None and lease.revision is not None:
                self._revision = lease.revision

    @staticmethod
    def _normalized(x,y,size):
        if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (x,y)) or not (0<=x<size[0] and 0<=y<size[1]):
            raise ValueError('Pixel coordinates must be inside the current screenshot')
        return x/(size[0]-1),y/(size[1]-1)

    def _at(self, x,y,target, action, modifiers=()):
        if not isinstance(modifiers,(tuple,list)) or any(key not in MODIFIERS for key in modifiers):
            raise ValueError('Invalid keyboard modifiers')
        with self._mutex:
            deadline = time.monotonic()+self.timeout
            while True:
                match = self.wait_for(target,timeout=max(0,deadline-time.monotonic())) if target is not None else None
                retry = None
                with self._input() as session:
                    frame = self.screenshot()
                    scale = 1.
                    if target is not None:
                        try:
                            fresh = locate(frame,target)
                            if max(abs(fresh.point[i]-match.point[i]) for i in (0,1))>3:
                                raise TargetNotFound('Target moved immediately before input')
                            x,y = fresh.point
                            scale = fresh.scale
                        except TargetNotFound as exc:
                            retry = exc
                    if retry is None:
                        nx,ny = self._normalized(x,y,frame.size)
                        try:
                            if modifiers:session.keys(list(modifiers))
                            return action(session,nx,ny,frame.size,scale)
                        finally:
                            if modifiers:session.keys([])
                # Only retry observation failures BEFORE input. A failed input
                # may already have taken effect and must never be duplicated.
                if time.monotonic()>=deadline:raise retry
                self._pause(self.poll_interval)

    def click(self,x=None,y=None,*,target=None,button='left',clicks=1,interval=.12,modifiers=(),hold_ms=30):
        if button not in ('left','right','middle') or type(clicks) is not int or not 1<=clicks<=3:
            raise ValueError('Invalid click button/count')
        if not 0<=interval<=1 or (target is None and (x is None or y is None)):
            raise ValueError('Provide a target or pixel coordinates')
        if type(hold_ms) is not int or not 1<=hold_ms<=30000:
            raise ValueError('hold_ms must be 1..30000')
        def action(session,nx,ny,size,scale):
            for index in range(clicks):
                self._check()
                if hold_ms==30:
                    session.click(nx,ny,button,0)
                else:
                    try:
                        session.pointer(nx,ny,{'left':1,'right':2,'middle':4}[button])
                        self._pause(hold_ms/1000)
                    finally:
                        session.pointer(nx,ny,0)
                if index+1<clicks:self._pause(interval)
        return self._at(x,y,target,action,modifiers)

    def move_to(self,x,y):
        return self._at(x,y,None,lambda device,nx,ny,size,scale:device.pointer(nx,ny))

    def scroll(self, clicks, *, target=None, x=None, y=None, modifiers=()):
        if type(clicks) is not int or not -2000<=clicks<=2000:
            raise ValueError('Scroll must be an integer in -2000..2000')
        def action(session,nx,ny,size,scale):
            remaining = clicks
            while remaining:
                self._check()
                amount = max(-127,min(127,remaining))
                session.pointer(nx,ny,0,amount)
                remaining -= amount
        return self._at(x,y,target,action,modifiers)

    def _keyboard(self, target, action):
        if target is not None:
            return self._at(None,None,target,lambda device,*ignored:action(device))
        with self._mutex,self._input() as session:
            return action(session)

    def hotkey(self,*keys,hold_ms=50,target=None):
        return self._keyboard(target,lambda device:device.press_key(list(keys),hold_ms))

    def press(self,key,hold_ms=50):
        self.hotkey(key,hold_ms=hold_ms)

    def write(self,text,*,target=None):
        return self._keyboard(target,lambda device:device.type_text(text))

    def key_sequence(self, events, *, target=None):
        if not isinstance(events,list) or not events or len(events)>10000:
            raise ValueError('Invalid key sequence')
        for event in events:
            if (set(event)!={'keys','delay'} or not isinstance(event['keys'],list)
                    or not all(isinstance(k,str) and 1<=len(k)<=32 for k in event['keys'])
                    or not isinstance(event['delay'],(int,float)) or not 0<=event['delay']<=30):
                raise ValueError('Invalid key event')
        if events[-1]['keys']:
            raise ValueError('Key sequence must end with all keys released')
        def action(session):
            try:
                for event in events:
                    self._pause(event['delay'])
                    session.keys(event['keys'])
            finally:
                session.keys([])
        return self._keyboard(target,action)

    def drag(self, *, target: VisualTarget, path, button='left', modifiers=()):
        buttons={'left':1,'right':2,'middle':4,'none':0}
        if button not in buttons or not isinstance(path,list) or not 1<=len(path)<=2000:
            raise ValueError('Invalid drag')
        for point in path:
            if (set(point)!={'dx','dy','delay'} or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in point.values())
                    or not 0<=point['delay']<=30):
                raise ValueError('Invalid drag point')
        def action(session,nx,ny,size,scale):
            origin=(nx*(size[0]-1),ny*(size[1]-1))
            positions=[self._normalized(origin[0]+p['dx']*scale,origin[1]+p['dy']*scale,size) for p in path]
            last=(nx,ny)
            try:
                session.pointer(nx,ny,buttons[button])
                for point,position in zip(path,positions):
                    self._pause(point['delay'])
                    session.pointer(*position,buttons[button])
                    last=position
            finally:
                session.pointer(*last,0)
        return self._at(None,None,target,action,modifiers)

    def move_path(self, *, target, path, modifiers=()):
        return self.drag(target=target,path=path,button='none',modifiers=modifiers)

    def replay(self, recording):
        from kvm.automation.recording import Recording
        flow = recording if isinstance(recording,Recording) else Recording.load(recording)
        for step in flow.steps:
            self._check()
            args = dict(step['args'])
            if 'target' in args:args['target']=flow.target(args['target'])
            if step['action']=='hotkey':
                self.hotkey(*args.pop('keys'),**args)
            else:
                getattr(self,step['action'])(**args)
