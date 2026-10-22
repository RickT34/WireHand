"""InputSession decorator: capture before input and record successful delivery."""
from contextlib import contextmanager


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
            self.recorder.before_keys()
            self.inner.keys(keys)
            self.recorder.keys_sent(keys)

    def pointer(self,x,y,buttons=0,wheel=0):
        with self._guard():
            frame=self.recorder.before_pointer(x,y,buttons,wheel)
            self.inner.pointer(x,y,buttons,wheel)
            self.recorder.pointer_sent(x,y,buttons,wheel,frame)

    def press_key(self,keys,hold_ms=50):
        with self._guard():
            self.recorder._flush_keys();self.recorder._flush_motion()
            started=self.recorder.clock()
            self.inner.press_key(keys,hold_ms)
            self.recorder.semantic('hotkey',{'keys':list(keys),'hold_ms':hold_ms},started)

    def type_text(self,text):
        with self._guard():
            self.recorder._flush_keys();self.recorder._flush_motion()
            started=self.recorder.clock()
            self.inner.type_text(text)
            self.recorder.semantic('write',{'text':text},started)

    def click(self,x,y,button='none',wheel=0):
        buttons={'none':0,'left':1,'right':2,'middle':4}
        with self._guard():
            if button not in buttons or (button!='none' and wheel):
                raise ValueError('Record clicks and scroll separately')
            self.recorder._flush_keys();self.recorder._flush_motion()
            frame=self.recorder.before_pointer(x,y,buttons[button],wheel)
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
