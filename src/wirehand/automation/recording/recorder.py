"""Group input events into semantic steps and manage recording lifecycle."""
import json
import math
from pathlib import Path
import time

from wirehand.core.keys import MODIFIERS
from wirehand.vision.images import decode
from .anchors import AnchorStore
from .script import generate_script
from .session import RecordingSession


class Recorder:
    """Explicit recording only. Input is delivered by the wrapped InputSession.

    Only mouse presses capture a frame. Motion is grouped into hover/drag paths;
    keyboard event timing and modifier-clicks are preserved. No global key hook.
    """
    def __init__(self, backend, directory, *, detector=None, clock=time.monotonic):
        self.backend,self.directory = backend,Path(directory)
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=False)
        (self.directory/'assets').mkdir(mode=0o700)
        self.clock = clock
        self.data = {'version':1,'status':'recording','targets':{},'steps':[],'warnings':[]}
        self.anchors = AnchorStore(self.directory, self.data['targets'], detector)
        self.keys = []
        self.position = (0.,0.)
        self.buttons = 0
        self.motion = None
        self.key_events = []
        self.keys_started = None
        self.last_finished = self.clock()
        self.last_key_time = None
        self._last_scroll = None
        self.error = None
        self._save()

    def _save(self):
        temporary = self.directory/'recording.tmp'
        temporary.write_text(json.dumps(self.data,ensure_ascii=False,indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.directory/'recording.json')

    def frame(self):
        return decode(self.backend.screenshot(3840))


    def _append(self, action, *, started, finished, **args):
        if len(self.data['steps'])>=500:raise ValueError('Recording reached 500 steps')
        delay = round(max(0., started-self.last_finished),4)
        self.data['steps'].append({'action':action,'args':args,'delay':delay})
        self.last_finished = finished
        self._save()

    def _flush_keys(self):
        if not self.key_events:return
        modifier_tap = not self.keys and any(event['keys'] for event in self.key_events)
        if modifier_tap or any(set(event['keys'])-MODIFIERS for event in self.key_events):
            if set(self.keys)-MODIFIERS:
                raise ValueError('Release character keys before pointer actions while recording')
            events = list(self.key_events)
            if events[-1]['keys']:events.append({'keys':[],'delay':0.})
            self._append('key_sequence',events=events,started=self.keys_started,finished=self.last_key_time)
        self.key_events,self.keys_started,self.last_key_time = [],None,None

    def _flush_motion(self):
        if self.motion is None:return
        motion,self.motion = self.motion,None
        start,frame,path = motion['start'],motion['frame'],motion['path']
        if not motion['buttons']:
            points = [{'x':start[0],'y':start[1],'delay':0.}, *path]
            self._append('move_path',path=points,modifiers=motion['modifiers'],
                         started=motion['started'],finished=motion['last'])
            self._last_click = None
            return
        point = (start[0]*(frame.width-1),start[1]*(frame.height-1))
        distance = max((math.hypot(p['dx'],p['dy']) for p in path),default=0)
        if motion['buttons'] and distance<4:
            button = {1:'left',2:'right',4:'middle'}[motion['buttons']]
            held = round((motion['last']-motion['started'])*1000)
            previous = self.data['steps'][-1] if self.data['steps'] else None
            if (previous and previous['action']=='click' and self._last_click is not None
                    and self.clock()-self._last_click['time']<.55 and held<300
                    and math.hypot(point[0]-self._last_click['point'][0],point[1]-self._last_click['point'][1])<4
                    and previous['args'].get('modifiers',[])==motion['modifiers'] and previous['args']['button']==button
                    and previous['args'].get('clicks',1)<3):
                previous['args']['clicks']=previous['args'].get('clicks',1)+1
                previous['args']['interval']=.12
                self.last_finished = motion['last']
                self._save()
            else:
                hold = {'hold_ms':held} if held>=300 else {}
                self._append('click',target=self.anchors.capture(frame,point),button=button,
                             modifiers=motion['modifiers'],started=motion['started'],finished=motion['last'],**hold)
            self._last_click={'time':self.clock(),'point':point} if held<300 else None
        else:
            if not path:path=[{'dx':0.,'dy':0.,'delay':0.}]
            self._append('drag',target=self.anchors.capture(frame,point),path=path,
                         modifiers=motion['modifiers'],button={1:'left',2:'right',4:'middle'}[motion['buttons']],
                         started=motion['started'],finished=motion['last'])
            self._last_click=None

    _last_click = None

    def before_keys(self):
        if self.buttons:
            raise ValueError('Finish the drag before changing keyboard state while recording')
        self._flush_motion()

    def keys_sent(self, keys):
        now=self.clock()
        if not self.key_events:self.keys_started=now
        delay=0. if self.last_key_time is None else now-self.last_key_time
        if self.keys and delay>30:raise ValueError('A recorded key hold cannot exceed 30 seconds')
        if not self.keys:delay=min(delay,30.)
        self.key_events.append({'keys':list(keys),'delay':round(delay,4)})
        if len(self.key_events)>10000:raise ValueError('Recording keyboard batch too long')
        self.last_key_time,self.keys=now,list(keys)
        self._last_click=None
        self._last_scroll=None

    def before_pointer(self,x,y,buttons,wheel):
        self._flush_keys()
        if buttons not in (0,1,2,4):raise ValueError('Recording simultaneous mouse buttons is unsupported')
        releasing = self.buttons and not buttons
        if self.motion and ((buttons!=self.buttons and not releasing) or wheel):self._flush_motion()
        # Capture the pre-input image exactly once on a new mouse press.
        return self.frame() if buttons and buttons!=self.buttons else None

    def pointer_sent(self,x,y,buttons,wheel,frame):
        now=self.clock()
        if wheel:
            point=(x,y)
            previous=self.data['steps'][-1] if self.data['steps'] else None
            if (previous and previous['action']=='scroll' and self._last_scroll is not None
                    and now-self._last_scroll['time']<.3
                    and math.hypot(point[0]-self._last_scroll['point'][0],point[1]-self._last_scroll['point'][1])<.01
                    and previous['args'].get('modifiers',[])==list(self.keys)
                    and previous['args']['clicks']*wheel>0
                    and abs(previous['args']['clicks']+wheel)<=2000):
                previous['args']['clicks']+=wheel
                self.last_finished=now
                self._save()
            else:
                self._append('scroll',clicks=wheel,position=[x,y],modifiers=list(self.keys),
                             started=now,finished=now)
            self._last_scroll={'time':now,'point':point}
            self._last_click=None
        elif self.motion is None:
            # A button release completes the preceding gesture; don't invent an
            # extra hover step at its ending coordinate.
            if not (self.buttons and not buttons):
                self.motion={'start':(x,y),'frame':frame,'path':[],'buttons':buttons,
                             'modifiers':list(self.keys),'last':now,'started':now}
        else:
            motion=self.motion
            if now-motion['last']>30 and self.buttons:
                raise ValueError('A recorded pointer hold cannot exceed 30 seconds')
            delay = round(min(30.,now-motion['last']),4)
            if motion['buttons']:
                point = {'dx':round((x-motion['start'][0])*(motion['frame'].width-1),3),
                         'dy':round((y-motion['start'][1])*(motion['frame'].height-1),3),'delay':delay}
            else:
                point = {'x':x,'y':y,'delay':delay}
            motion['path'].append(point)
            if len(motion['path'])+(0 if motion['buttons'] else 1)>2000:raise ValueError('Recording pointer gesture too long')
            motion['last']=now
            if self.buttons and not buttons:self._flush_motion()
        self.position,self.buttons=(x,y),buttons

    def semantic(self, action, args, started):
        self._flush_keys();self._flush_motion()
        self._append(action,started=started,finished=self.clock(),**args)
        self._last_click=None

    def fail(self, error):
        self.error=str(error)
        self.data.update(status='failed',error=self.error)
        self._save()

    def finish(self, *, interrupted=False):
        if self.error:
            self.data.update(status='failed',error=self.error)
        else:
            self.keys=[]
            if self.key_events and self.key_events[-1]['keys']:
                self.key_events.append({'keys':[],'delay':0.})
            self._flush_keys();self._flush_motion()
            self.data['status']='interrupted' if interrupted else 'complete'
        self._save()
        script=self.directory/'replay.py'
        script.write_text(generate_script(self.data));script.chmod(0o600)
        return {'id':self.directory.name,'status':self.data['status'],'steps':len(self.data['steps']),
                'error':self.error}

    def wrap(self, session):
        return RecordingSession(session,self)
