"""Synchronous, PyAutoGUI-style operations over an injected device backend."""
from contextlib import contextmanager
import math
from pathlib import Path
import threading
import time

from wirehand.core.contracts import Backend
from wirehand.core.keys import MODIFIERS
from wirehand.core.ownership import InputGate
from wirehand.vision.matching import Match, TargetNotFound, VisualTarget, locate
from wirehand.vision.images import decode


class AutomationInterrupted(RuntimeError):
    """会话已取消、有人接管或其他输入会话改变了设备状态。"""


class Computer:
    """截图、视觉等待、键鼠操作与录制的同步接口。

    通过 `wirehand.connect()` 创建。坐标为截图像素；click 等操作也可以接收
    VisualTarget，自动等待稳定匹配并在发送输入前重新验证。
    匹配超时抛出 TargetNotFound；目标不唯一时抛出 AmbiguousTarget。
    有人接管后当前会话中断，需要重新创建会话。
    """
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
        """取消当前会话；可从其他线程调用。等待和后续输入会抛出 AutomationInterrupted。"""
        self._cancelled.set()

    def sleep(self, seconds):
        """等待 0–86400 秒，保留录制的操作间隔；取消或接管时中断等待。"""
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<=seconds<=86400:
            raise ValueError('seconds must be finite and in 0..86400')
        self._check()
        deadline = time.monotonic()+seconds
        while (remaining := deadline-time.monotonic())>0:
            self._pause(min(self.poll_interval,remaining))

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
        """返回 PIL RGB 图像；最大宽度 3840，保持屏幕比例。"""
        return decode(self.backend.screenshot(3840))

    def size(self):
        """返回当前截图的 `(width, height)`。"""
        return self.screenshot().size

    def locate(self,target):
        """在当前截图中立即匹配目标，返回 Match；不等待、不发送输入。"""
        return locate(self.screenshot(),target)

    @contextmanager
    def record(self, directory):
        """录制此 Computer 发送的操作，退出上下文时生成 replay.py 和目标图片。

        Args:
            directory: 新建的输出目录，必须尚不存在。

        ```python
        with computer.record('my-flow'):
            computer.click(100, 80)
            computer.write('hello')
        ```
        """
        from wirehand.automation.recording import Recorder
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
        """等待目标连续两次稳定出现，返回匹配位置。

        Args:
            target: 通过 Recording.target 或 VisualTarget 创建的目标。
            timeout: 最长等待秒数；None 使用会话默认值。
        """
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
        """点击像素坐标或视觉目标；提供 target 时忽略 x/y。

        Args:
            x: 水平像素坐标。
            y: 垂直像素坐标。
            target: 可选 VisualTarget，自动等待并定位。
            button: left、right 或 middle。
            clicks: 点击次数，1–3；2 表示双击。
            interval: 多次点击间隔，0–1 秒。
            modifiers: 点击时按住的修饰键，例如 ['ctrl']。
            hold_ms: 按住鼠标的时长，1–30000 毫秒。
        """
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
        """将指针移动到像素坐标，不按下鼠标按钮。"""
        return self._at(x,y,None,lambda device,nx,ny,size,scale:device.pointer(nx,ny))

    def scroll(self, clicks, *, target=None, x=None, y=None, position=None, modifiers=()):
        """在目标或坐标处滚动；clicks 为 -2000–2000 整数，正数向上。

        position=(x, y) 表示 0–1 的屏幕相对坐标，用于无视觉目标的录制重放。
        它不需要截图，不能与 target 或像素 x/y 同时指定。
        """
        if type(clicks) is not int or not -2000<=clicks<=2000:
            raise ValueError('Scroll must be an integer in -2000..2000')
        def action(session,nx,ny,size,scale):
            remaining = clicks
            while remaining:
                self._check()
                amount = max(-127,min(127,remaining))
                session.pointer(nx,ny,0,amount)
                remaining -= amount
        if position is not None:
            if target is not None or x is not None or y is not None:
                raise ValueError('position cannot be combined with a target or pixel coordinates')
            if not isinstance(position,(tuple,list)) or len(position)!=2 or not all(type(v) in (int,float) and 0<=v<=1 for v in position):
                raise ValueError('position must contain two normalized coordinates')
            with self._plain_pointer(modifiers) as session:
                return action(session,*position,None,1.)
        return self._at(x,y,target,action,modifiers)

    @contextmanager
    def _plain_pointer(self, modifiers):
        if not isinstance(modifiers,(tuple,list)) or any(key not in MODIFIERS for key in modifiers):
            raise ValueError('Invalid keyboard modifiers')
        with self._mutex,self._input() as session:
            try:
                if modifiers:session.keys(list(modifiers))
                yield session
            finally:
                if modifiers:session.keys([])

    def _keyboard(self, target, action):
        if target is not None:
            return self._at(None,None,target,lambda device,*ignored:action(device))
        with self._mutex,self._input() as session:
            return action(session)

    def hotkey(self,*keys,hold_ms=50,target=None):
        """按下并释放组合键，如 hotkey('ctrl', 'a')；target 可用于等待界面就绪。"""
        return self._keyboard(target,lambda device:device.press_key(list(keys),hold_ms))

    def press(self,key,hold_ms=50):
        """按下并释放一个键，如 enter、esc、tab、left、f2。"""
        self.hotkey(key,hold_ms=hold_ms)

    def write(self,text,*,target=None):
        """键盘输入文本；可用字符取决于平台键盘实现和目标输入法。

        target 用于等待界面就绪，不会自动点击输入框，请先 click。
        """
        return self._keyboard(target,lambda device:device.type_text(text))

    def key_sequence(self, events, *, target=None):
        """执行键盘状态序列，保留录制时序。

        events 为 [{'keys': ['ctrl', 'a'], 'delay': 0.1}, {'keys': [], 'delay': 0.1}]。
        每项 delay 为 0–30 秒，最后一项必须释放全部按键。
        """
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
        """从视觉目标开始拖动；path 中 dx/dy 为相对起点的像素偏移，delay 为秒。

        例如 path=[{'dx': 100, 'dy': 20, 'delay': 0.3}]。
        屏幕缩放时偏移随匹配比例缩放，结束或异常时释放按钮。
        """
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

    def move_path(self, *, path, target=None, modifiers=()):
        """不按下鼠标按钮，按记录的路径与间隔移动。

        不提供 target 时，path=[{'x':0.5,'y':0.5,'delay':0.1}] 使用 0–1 屏幕相对坐标，
        不截图或进行视觉匹配。提供 target 时使用与 drag 相同的相对像素 dx/dy。
        """
        if target is not None:
            return self.drag(target=target,path=path,button='none',modifiers=modifiers)
        if not isinstance(path,list) or not 1<=len(path)<=2000:
            raise ValueError('Invalid pointer path')
        for point in path:
            if (not isinstance(point,dict) or set(point)!={'x','y','delay'}
                    or not all(type(point[k]) in (int,float) and 0<=point[k]<=1 for k in ('x','y'))
                    or type(point['delay']) not in (int,float) or not 0<=point['delay']<=30):
                raise ValueError('Invalid normalized pointer path')
        with self._plain_pointer(modifiers) as session:
            for point in path:
                self._pause(point['delay'])
                session.pointer(point['x'],point['y'])

    def replay(self, recording):
        """执行 Recording 或录制目录中的语义步骤；不会执行目录中的 replay.py。

        此方法适合原始录制数据。网页脚本库执行 replay.py，因此支持手工修改脚本逻辑。
        """
        from wirehand.automation.recording import Recording
        flow = recording if isinstance(recording,Recording) else Recording.load(recording)
        for step in flow.steps:
            self._check()
            if step.get('delay',0):self.sleep(step['delay'])
            args = dict(step['args'])
            if 'target' in args:args['target']=flow.target(args['target'])
            if step['action']=='hotkey':
                self.hotkey(*args.pop('keys'),**args)
            else:
                getattr(self,step['action'])(**args)
