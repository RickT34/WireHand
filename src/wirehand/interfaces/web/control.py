"""Exclusive browser control, held only for the lifetime of its connection."""
from contextlib import ExitStack
from pathlib import Path

from wirehand.core.contracts import Backend
from wirehand.core.ownership import InputGate
from wirehand.automation.scripts.library import ScriptLibrary

DOM_KEYS = {f'Key{c.upper()}': c for c in 'abcdefghijklmnopqrstuvwxyz'}
DOM_KEYS.update({f'Digit{c}': c for c in '1234567890'})
DOM_KEYS.update({f'F{i}': f'f{i}' for i in range(1, 13)})
DOM_KEYS.update(dict(zip(
    ['Enter','Escape','Backspace','Tab','Space','Minus','Equal','BracketLeft','BracketRight',
     'Backslash','Semicolon','Quote','Backquote','Comma','Period','Slash','CapsLock',
     'PrintScreen','ScrollLock','Pause','Insert','Home','PageUp','Delete','End','PageDown',
     'ArrowRight','ArrowLeft','ArrowDown','ArrowUp','ControlLeft','ShiftLeft','AltLeft','MetaLeft',
     'ControlRight','ShiftRight','AltRight','MetaRight'],
    ['enter','esc','backspace','tab','space','-','=','[',']','\\',';',"'",'`',',','.','/',
     'capslock','printscreen','scrolllock','pause','insert','home','pageup','delete','end','pagedown',
     'right','left','down','up','ctrl','shift','alt','meta','rightctrl','rightshift','rightalt','rightmeta'])))


class Controller:
    def __init__(self, backend: Backend, gate: InputGate, recordings_dir=None):
        self.backend = backend
        self.gate = gate
        self.owner = None
        self.session = None
        self.resources = None
        self.keys = set()
        self.recordings_dir = Path(recordings_dir or gate.runtime/'recordings')
        self.library = ScriptLibrary(self.recordings_dir)
        self.recorder = None
        self.last_recording = None

    def recording_status(self):
        return {'active':self.recorder is not None,
                'steps':len(self.recorder.data['steps']) if self.recorder else 0,
                'last':self.last_recording}

    def start_recording(self, owner, name=None):
        if self.owner is not owner or self.owner is None:
            raise RuntimeError('请先接管控制，再开始录制')
        if self.recorder is not None:
            raise RuntimeError('录制已经开始')
        errors = self.release_keys()
        if errors:raise RuntimeError('; '.join(errors))
        self.recorder = self.library.record(self.backend, name)
        self.session = self.recorder.wrap(self.session)

    def stop_recording(self, owner, *, interrupted=False):
        if self.owner is not owner or self.owner is None:
            raise RuntimeError('仅当前控制者可以停止录制')
        if self.recorder is None:
            raise RuntimeError('尚未开始录制')
        recorder, wrapper = self.recorder,self.session
        try:
            try:
                errors = self.release_keys()
                if errors:recorder.fail('; '.join(errors))
                result = recorder.finish(interrupted=interrupted)
            except (OSError,ValueError,RuntimeError) as exc:
                recorder.fail(exc)
                result = recorder.finish(interrupted=True)
            self.last_recording = result
            return result
        finally:
            self.session = wrapper.inner
            self.recorder = None

    @property
    def marker(self):
        return self.gate.marker

    def status(self):
        return {'manual': self.gate.manual, 'connected': self.owner is not None}

    def claim(self, owner):
        if self.owner is not None:
            if self.owner is owner:
                return
            raise RuntimeError('另一个浏览器正在控制')
        with ExitStack() as resources:
            resources.enter_context(self.gate.human())
            session = resources.enter_context(self.backend.open_input())
            session.release_all()
            self.resources = resources.pop_all()
            self.session, self.owner = session, owner
            self.keys.clear()

    def release_keys(self):
        self.keys.clear()
        try:
            if self.session is not None:
                self.session.release_all()
        except (OSError, ValueError, RuntimeError) as exc:
            return [str(exc)]
        return []

    def disconnect(self, owner):
        if self.owner is None or self.owner is not owner:
            return []
        errors = []
        if self.recorder:
            try:
                self.stop_recording(owner,interrupted=True)
            except (OSError,ValueError,RuntimeError) as exc:
                errors.append(str(exc))
        errors += self.release_keys()
        try:
            self.resources.close()
        except (OSError, RuntimeError) as exc:
            errors.append(str(exc))
        finally:
            self.session = self.resources = self.owner = None
        return errors

    def give_back(self, owner):
        if self.owner is not None and self.owner is not owner:
            raise RuntimeError('仅当前控制者可以交还控制权')
        if self.owner is None:
            self.recover_stale()
            return
        if self.recorder:
            self.stop_recording(owner)
        errors = self.disconnect(owner)
        if errors:
            raise RuntimeError('接管已结束，但释放键鼠失败：' + '; '.join(errors))

    def recover_stale(self):
        """Reset an abandoned session on startup without stealing a live lock."""
        if self.marker.exists():
            with self.gate.human(), self.backend.open_input() as session:
                session.release_all()

    def action(self, owner, message):
        if self.owner is not owner:
            raise RuntimeError('请先接管控制')
        kind = message.get('type')
        if kind == 'release':
            errors = self.release_keys()
            if errors:
                raise RuntimeError('; '.join(errors))
        elif kind == 'key':
            code = message.get('code')
            if code not in DOM_KEYS or type(message.get('down')) is not bool:
                raise ValueError('不支持此按键')
            keys = self.keys | {code} if message['down'] else self.keys - {code}
            self.session.keys([DOM_KEYS[k] for k in sorted(keys)])
            self.keys = keys
        elif kind == 'pointer':
            x, y, buttons, wheel = (message.get(k, 0) for k in ('x','y','buttons','wheel'))
            if type(x) not in (int, float) or type(y) not in (int, float):
                raise ValueError('Invalid coordinates')
            if type(buttons) is not int or type(wheel) is not int:
                raise ValueError('Invalid buttons/wheel')
            self.session.pointer(x, y, buttons, wheel)
        elif kind == 'shortcut':
            choices = {'ctrl-alt-delete': ['ctrl','alt','delete'], 'alt-tab': ['alt','tab'],
                       'meta': ['meta'], 'escape': ['esc']}
            if message.get('name') not in choices:
                raise ValueError('Unknown shortcut')
            errors = self.release_keys()
            if errors:
                raise RuntimeError('; '.join(errors))
            self.session.press_key(choices[message['name']], 50)
        else:
            raise ValueError('Unknown input event')
