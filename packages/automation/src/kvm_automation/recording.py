"""Record semantic input with visual anchors; export data and editable Python."""
from contextlib import contextmanager
import json
import math
from pathlib import Path
import time

from PIL import Image

from .targets import VisualTarget, TargetNotFound, locate
from .vision.detect import ContourDetector
from .vision.render import padded
from .vision.service import decode, changed_fraction

MODIFIERS = {'ctrl','shift','alt','meta','rightctrl','rightshift','rightalt','rightmeta'}
ACTIONS = {'click','scroll','move_path','drag','key_sequence','hotkey','write'}
PARAMETERS = {
    'click':({'target'},{'target','button','clicks','interval','modifiers','hold_ms'}),
    'scroll':({'target','clicks'},{'target','clicks','modifiers'}),
    'move_path':({'target','path'},{'target','path','modifiers'}),
    'drag':({'target','path'},{'target','path','button','modifiers'}),
    'key_sequence':({'target','events'},{'target','events'}),
    'hotkey':({'target','keys'},{'target','keys','hold_ms'}),
    'write':({'target','text'},{'target','text'}),
}


def validate_step(step):
    action,args=step.get('action'),step.get('args')
    if action not in ACTIONS or not isinstance(args,dict):
        raise ValueError('Unsupported recording action')
    required,allowed=PARAMETERS[action]
    if not required<=args.keys() or args.keys()-allowed:
        raise ValueError('Invalid recording action parameters')
    if not isinstance(args['target'],str):raise ValueError('Invalid target reference')
    modifiers=args.get('modifiers',[])
    if not isinstance(modifiers,list) or any(k not in MODIFIERS for k in modifiers):
        raise ValueError('Invalid modifiers')
    if args.get('button','left') not in ('left','right','middle'):
        raise ValueError('Invalid mouse button')
    if 'clicks' in args:
        limit=(1,3) if action=='click' else (-2000,2000)
        if type(args['clicks']) is not int or not limit[0]<=args['clicks']<=limit[1]:
            raise ValueError('Invalid click/scroll count')
    if 'interval' in args and (not isinstance(args['interval'],(int,float)) or not 0<=args['interval']<=1):
        raise ValueError('Invalid click interval')
    if action=='click' and (type(args.get('hold_ms',30)) is not int or not 1<=args.get('hold_ms',30)<=30000):
        raise ValueError('Invalid mouse hold time')
    if action=='write' and (not isinstance(args['text'],str) or not 1<=len(args['text'])<=1000):
        raise ValueError('Invalid recorded text')
    if action=='hotkey':
        if not isinstance(args['keys'],list) or not args['keys'] or not all(isinstance(k,str) for k in args['keys']):
            raise ValueError('Invalid hotkey')
        if type(args.get('hold_ms',50)) is not int or not 10<=args.get('hold_ms',50)<=1000:
            raise ValueError('Invalid hold time')
    for field in ('events','path'):
        if field not in args:continue
        entries=args[field]
        if not isinstance(entries,list) or not 1<=len(entries)<=(10000 if field=='events' else 2000):
            raise ValueError('Invalid event sequence')
        for entry in entries:
            if not isinstance(entry,dict) or set(entry)!=({'keys','delay'} if field=='events' else {'dx','dy','delay'}):
                raise ValueError('Invalid event fields')
            if not isinstance(entry['delay'],(int,float)) or not 0<=entry['delay']<=30:
                raise ValueError('Invalid event delay')
            if field=='events':
                if not isinstance(entry['keys'],list) or not all(isinstance(k,str) for k in entry['keys']):
                    raise ValueError('Invalid key state')
            elif not all(isinstance(entry[k],(int,float)) and math.isfinite(entry[k]) for k in ('dx','dy')):
                raise ValueError('Invalid drag coordinates')
        if field=='events' and entries[-1]['keys']:
            raise ValueError('Key sequence leaves keys held')


def _safe_asset(directory, name):
    path = (directory/name).resolve()
    if not path.is_relative_to(directory.resolve()) or path.suffix.lower()!='.png':
        raise ValueError('Recording assets must be PNGs inside the recording directory')
    return path


class Recording:
    def __init__(self, directory, data):
        self.directory,self.data = Path(directory),data
        self.steps = data['steps']

    @classmethod
    def load(cls, directory, *, allow_incomplete=False):
        directory = Path(directory)
        raw = (directory/'recording.json').read_bytes()
        if len(raw)>8*1024*1024:
            raise ValueError('Recording manifest too large')
        data = json.loads(raw)
        if data.get('version')!=1 or (data.get('status')!='complete' and not allow_incomplete):
            raise ValueError('Recording is incomplete or unsupported; review it before replay')
        if not isinstance(data.get('steps'),list) or len(data['steps'])>500:
            raise ValueError('Invalid recording steps')
        for target in data.get('targets',{}).values():
            path = _safe_asset(directory,target['image'])
            with Image.open(path) as image:
                x,y = target['point']
                if not (0<=x<image.width and 0<=y<image.height):
                    raise ValueError('Invalid target point')
            if len(target['screen_size'])!=2 or not all(type(v) is int and v>=4 for v in target['screen_size']):
                raise ValueError('Invalid reference screen size')
        for step in data['steps']:
            validate_step(step)
            identity = step['args'].get('target')
            if identity and identity not in data['targets']:
                raise ValueError('Unknown target reference')
        return cls(directory,data)

    def target(self, identity):
        value = self.data['targets'][identity]
        return VisualTarget(_safe_asset(self.directory,value['image']),tuple(value['point']),
                            tuple(value['screen_size']),strategy=value['strategy'])

    def replay(self, computer):
        computer.replay(self)


def generate_script(data):
    lines = ['"""Generated visual replay. Set KVM_BACKEND before running."""',
             'from pathlib import Path','from kvm_automation import Computer, Recording','',
             'flow = Recording.load(Path(__file__).parent)','',
             'with Computer.connect() as computer:']
    for step in data['steps']:
        args = dict(step['args'])
        target = args.pop('target',None)
        params = []
        if step['action']=='hotkey':
            params.extend(repr(k) for k in args.pop('keys'))
        if target:params.append(f'target=flow.target({target!r})')
        params.extend(f'{key}={value!r}' for key,value in args.items())
        lines.append(f'    computer.{step["action"]}({", ".join(params)})')
    if not data['steps']:lines.append('    pass')
    return '\n'.join(lines)+'\n'


class Recorder:
    """Explicit recording only. Input is delivered by the wrapped InputSession.

    Frames are captured before actions. Motion is grouped into hover/drag paths;
    keyboard event timing and modifier-clicks are preserved. No global key hook.
    """
    def __init__(self, backend, directory, *, detector=None, clock=time.monotonic):
        self.backend,self.directory = backend,Path(directory)
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=False)
        (self.directory/'assets').mkdir(mode=0o700)
        self.detector = detector or ContourDetector()
        self.clock = clock
        self.data = {'version':1,'status':'recording','targets':{},'steps':[],'warnings':[]}
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
        self.bytes_saved = 0
        self._save()

    def _save(self):
        temporary = self.directory/'recording.tmp'
        temporary.write_text(json.dumps(self.data,ensure_ascii=False,indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.directory/'recording.json')

    def frame(self):
        return decode(self.backend.screenshot(3840))

    def _target(self, frame, point, *, scene=False):
        width,height = frame.size
        x,y = point
        boxes = [] if scene else [c.box for c in self.detector.detect(frame)
                                   if c.box[0]-8<=x<c.box[2]+8 and c.box[1]-8<=y<c.box[3]+8]
        box = min(boxes,key=lambda b:(b[2]-b[0])*(b[3]-b[1])) if boxes else (int(x)-24,int(y)-20,int(x)+25,int(y)+21)
        box = (min(box[0],int(x)),min(box[1],int(y)),max(box[2],int(x)+1),max(box[3],int(y)+1))
        identity = f'target_{len(self.data["targets"])+1:04}'
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
        self.data['targets'][identity] = {'image':f'assets/{identity}.png','point':list(offset),
                                         'screen_size':list(frame.size),'box':list(bounds),
                                         'strategy':strategy}
        return identity

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
            return self._target(frame,(self.focus_point[0]*(frame.width-1),self.focus_point[1]*(frame.height-1)))
        return self._target(frame,(frame.width/2,frame.height/2),scene=True)

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
        identity = self._target(frame,point)
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
                self._append('scroll',clicks=wheel,target=self._target(frame,point),modifiers=list(self.keys))
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


class RecordingSession:
    def __init__(self, inner, recorder):
        self.inner,self.recorder=inner,recorder

    @contextmanager
    def _guard(self, *, cleanup=False):
        if self.recorder.error and not cleanup:
            raise RuntimeError('Recording failed; stop it before sending more recorded input')
        try:
            yield
        except Exception as exc:
            self.recorder.fail(exc)
            raise

    def keys(self,keys):
        with self._guard():
            frame=self.recorder.before_keys()
            self.inner.keys(keys)
            self.recorder.keys_sent(keys,frame)

    def pointer(self,x,y,buttons=0,wheel=0):
        with self._guard():
            frame=self.recorder.before_pointer(x,y,buttons,wheel)
            self.inner.pointer(x,y,buttons,wheel)
            self.recorder.pointer_sent(x,y,buttons,wheel,frame)

    def press_key(self,keys,hold_ms=50):
        with self._guard():
            self.recorder._flush_keys();self.recorder._flush_motion()
            frame=self.recorder.frame()
            self.inner.press_key(keys,hold_ms)
            self.recorder.semantic('hotkey',{'keys':list(keys),'hold_ms':hold_ms},frame)

    def type_text(self,text):
        with self._guard():
            self.recorder._flush_keys();self.recorder._flush_motion()
            frame=self.recorder.frame()
            self.inner.type_text(text)
            self.recorder.semantic('write',{'text':text},frame)

    def click(self,x,y,button='none',wheel=0):
        buttons={'none':0,'left':1,'right':2,'middle':4}
        with self._guard():
            if button not in buttons or (button!='none' and wheel):
                raise ValueError('Record clicks and scroll separately')
            self.recorder._flush_keys();self.recorder._flush_motion()
            frame=self.recorder.frame()
            self.inner.click(x,y,button,wheel)
            self.recorder.pointer_sent(x,y,buttons[button],wheel,frame)
            if button!='none':self.recorder.pointer_sent(x,y,0,0,None)

    def release_all(self):
        with self._guard(cleanup=True):
            self.inner.release_all()
            if self.recorder.error:return
            if self.recorder.key_events:self.recorder.keys_sent([])
            self.recorder.keys=[]
            if self.recorder.buttons and self.recorder.motion:
                now=self.recorder.clock()
                if now-self.recorder.motion['last']>30:
                    raise ValueError('A recorded pointer hold cannot exceed 30 seconds')
                self.recorder.motion['last']=now
            self.recorder._flush_motion()
            self.recorder.buttons=0
