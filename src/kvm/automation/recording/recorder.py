"""Group input events into semantic steps and manage recording lifecycle."""
import json
import math
from pathlib import Path
import time

from kvm.core.keys import MODIFIERS
from kvm.vision.images import decode, changed_fraction
from .anchors import AnchorStore
from .script import generate_script
from .session import RecordingSession


class Recorder:
    """Explicit recording only. Input is delivered by the wrapped InputSession.

    Frames are captured before actions. Motion is grouped into hover/drag paths;
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
        self.focus_point = None
        self.buttons = 0
        self.motion = None
        self.key_events = []
        self.key_frame = None
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


    def _append(self, action, **args):
        if len(self.data['steps'])>=500:raise ValueError('Recording reached 500 steps')
        self.data['steps'].append({'action':action,'args':args})
        self._save()

    def _flush_keys(self):
        if not self.key_events:return
        modifier_tap = not self.keys and any(event['keys'] for event in self.key_events)
        if modifier_tap or any(set(event['keys'])-MODIFIERS for event in self.key_events):
            if set(self.keys)-MODIFIERS:
                raise ValueError('Release character keys before pointer actions while recording')
            events = list(self.key_events)
            if events[-1]['keys']:events.append({'keys':[],'delay':0.})
            identity = self._keyboard_target(self.key_frame)
            self._append('key_sequence',target=identity,events=events)
        self.key_events,self.key_frame,self.last_key_time = [],None,None

    def _keyboard_target(self, frame):
        if self.focus_point is not None:
            return self.anchors.capture(frame,(self.focus_point[0]*(frame.width-1),self.focus_point[1]*(frame.height-1)))
        return self.anchors.capture(frame,(frame.width/2,frame.height/2),scene=True)

    def _flush_motion(self):
        if self.motion is None:return
        motion,self.motion = self.motion,None
        start,frame,path = motion['start'],motion['frame'],motion['path']
        if not motion['buttons']:
            current = self.frame()
            if current.size==frame.size:
                size=(480,max(1,round(frame.height*480/frame.width)))
                if changed_fraction(frame.resize(size),current.resize(size))<.002:
                    return  # Plain travel is replaced by the next target lookup.
        point = (start[0]*(frame.width-1),start[1]*(frame.height-1))
        identity = self.anchors.capture(frame,point)
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
                self._save()
            else:
                hold = {'hold_ms':held} if held>=300 else {}
                self._append('click',target=identity,button=button,modifiers=motion['modifiers'],**hold)
            self._last_click={'time':self.clock(),'point':point} if held<300 else None
        else:
            if not path:path=[{'dx':0.,'dy':0.,'delay':0.}]
            args={'target':identity,'path':path,'modifiers':motion['modifiers']}
            if motion['buttons']:
                args['button']={1:'left',2:'right',4:'middle'}[motion['buttons']]
                self._append('drag',**args)
            else:
                self._append('move_path',**args)
            self._last_click=None

    _last_click = None

    def before_keys(self):
        if self.buttons:
            raise ValueError('Finish the drag before changing keyboard state while recording')
        self._flush_motion()
        return self.frame() if not self.key_events else None

    def keys_sent(self, keys, frame=None):
        now=self.clock()
        if not self.key_events:self.key_frame=frame
        delay=0. if self.last_key_time is None else now-self.last_key_time
        if self.keys and delay>30:raise ValueError('A recorded key hold cannot exceed 30 seconds')
        # Idle thinking pauses are handled by the next visual target wait.
        if not self.keys:delay=min(delay,.25)
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
        if wheel or self.motion is None:
            frame=self.frame()
        else:frame=None
        return frame

    def pointer_sent(self,x,y,buttons,wheel,frame):
        now=self.clock()
        if buttons==1 and self.buttons==0:self.focus_point=(x,y)
        if wheel:
            point=(x*(frame.width-1),y*(frame.height-1))
            previous=self.data['steps'][-1] if self.data['steps'] else None
            if (previous and previous['action']=='scroll' and self._last_scroll is not None
                    and now-self._last_scroll['time']<.3
                    and math.hypot(point[0]-self._last_scroll['point'][0],point[1]-self._last_scroll['point'][1])<8
                    and previous['args'].get('modifiers',[])==list(self.keys)
                    and previous['args']['clicks']*wheel>0
                    and abs(previous['args']['clicks']+wheel)<=2000):
                previous['args']['clicks']+=wheel
                self._save()
            else:
                self._append('scroll',clicks=wheel,target=self.anchors.capture(frame,point),modifiers=list(self.keys))
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
            motion['path'].append({'dx':round((x-motion['start'][0])*(motion['frame'].width-1),3),
                                   'dy':round((y-motion['start'][1])*(motion['frame'].height-1),3),
                                   'delay':round(min(30.,now-motion['last']),4)})
            if len(motion['path'])>2000:raise ValueError('Recording pointer gesture too long')
            motion['last']=now
            if self.buttons and not buttons:self._flush_motion()
        self.position,self.buttons=(x,y),buttons

    def semantic(self, action, args, frame):
        self._flush_keys();self._flush_motion()
        identity=self._keyboard_target(frame)
        self._append(action,target=identity,**args)
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
