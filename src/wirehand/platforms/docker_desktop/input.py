"""Translate the platform's logical keys and normalized pointer into XTEST."""
import math
import time

from Xlib import X, XK
from Xlib.ext import xtest

from wirehand.core.keys import MODIFIERS


KEYSYMS = dict(zip(
    ['ctrl', 'shift', 'alt', 'meta', 'rightctrl', 'rightshift', 'rightalt', 'rightmeta',
     'enter', 'esc', 'backspace', 'tab', 'space', 'capslock', 'printscreen', 'scrolllock',
     'pause', 'insert', 'home', 'pageup', 'delete', 'end', 'pagedown', 'right', 'left', 'down', 'up'],
    ['Control_L', 'Shift_L', 'Alt_L', 'Super_L', 'Control_R', 'Shift_R', 'Alt_R', 'Super_R',
     'Return', 'Escape', 'BackSpace', 'Tab', 'space', 'Caps_Lock', 'Print', 'Scroll_Lock',
     'Pause', 'Insert', 'Home', 'Prior', 'Delete', 'End', 'Next', 'Right', 'Left', 'Down', 'Up']))
KEYSYMS.update({f'f{i}': f'F{i}' for i in range(1, 13)})
BASE_KEYS = 'abcdefghijklmnopqrstuvwxyz0123456789-=[]\\;\'`,./ '
SHIFTED = dict(zip('!@#$%^&*()_+{}|:"~<>?', '1234567890-=[]\\;\'`,./'))
BUTTONS = ((1, 1), (2, 3), (4, 2))  # Protocol bit, X11 button.


class DesktopInput:
    def __init__(self, connection):
        self.connection = connection
        self.root = connection.screen().root

    def _event(self, kind, detail=0, **options):
        xtest.fake_input(self.connection, kind, detail, **options)

    def _codes(self, keys):
        normalized = [key.lower() for key in keys]
        if len(set(normalized) - MODIFIERS) > 6:
            raise ValueError('At most six non-modifier keys may be pressed together')
        normalized.sort(key=lambda key: key not in MODIFIERS)
        codes = []
        for key in normalized:
            if key in KEYSYMS:
                symbol = XK.string_to_keysym(KEYSYMS[key])
            elif len(key) == 1 and key in BASE_KEYS:
                symbol = ord(key)
            elif key in ('\n', '\t'):
                symbol = XK.string_to_keysym('Return' if key == '\n' else 'Tab')
            else:
                raise ValueError(f'Unknown key: {key}')
            code = self.connection.keysym_to_keycode(symbol)
            if not code:
                raise ValueError(f'Key is unavailable in this display layout: {key}')
            if code not in codes:
                codes.append(code)
        return codes

    def _set_codes(self, codes):
        bitmap = self.connection.query_keymap()
        held = {code for code in range(8, 256) if bitmap[code // 8] & (1 << (code % 8))}
        for code in sorted(held - set(codes)):
            self._event(X.KeyRelease, code)
        for code in codes:
            if code not in held:
                self._event(X.KeyPress, code)
        self.connection.sync()

    def keys(self, keys):
        self._set_codes(self._codes(keys))

    @staticmethod
    def _validate_pointer(x, y, buttons, wheel):
        if not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
            raise ValueError('x and y must be finite normalized coordinates in [0, 1]')
        if type(buttons) is not int or not 0 <= buttons <= 7 or type(wheel) is not int or not -127 <= wheel <= 127:
            raise ValueError('Invalid buttons or wheel')

    def pointer(self, x, y, buttons=0, wheel=0):
        self._validate_pointer(x, y, buttons, wheel)
        geometry = self.root.get_geometry()
        mask = self.root.query_pointer().mask
        self._event(X.MotionNotify, x=round(x * (geometry.width - 1)), y=round(y * (geometry.height - 1)))
        for bit, button in BUTTONS:
            held = bool(mask & (1 << (7 + button)))
            if held != bool(buttons & bit):
                self._event(X.ButtonPress if buttons & bit else X.ButtonRelease, button)
        for _ in range(abs(wheel)):
            button = 4 if wheel > 0 else 5
            self._event(X.ButtonPress, button)
            self._event(X.ButtonRelease, button)
        self.connection.sync()

    def press_key(self, keys, hold_ms=50):
        if not keys:
            raise ValueError('Provide at least one key')
        codes = self._codes(keys)
        if type(hold_ms) is not int or not 10 <= hold_ms <= 1000:
            raise ValueError('hold_ms must be 10..1000')
        try:
            self._set_codes(codes)
            time.sleep(hold_ms / 1000)
        finally:
            self._set_codes([])

    def type_text(self, text):
        if not isinstance(text, str) or not 1 <= len(text) <= 1000:
            raise ValueError('Provide 1..1000 characters')
        reports = []
        for char in text:
            if char in SHIFTED:
                keys = ['shift', SHIFTED[char]]
            elif 'A' <= char <= 'Z':
                keys = ['shift', char.lower()]
            elif char in BASE_KEYS or char in ('\n', '\t'):
                keys = [char]
            else:
                raise ValueError('type_text supports US-layout ASCII only; use the target IME for other languages')
            reports.append(self._codes(keys))
        try:
            for codes in reports:
                self._set_codes(codes)
                self._set_codes([])
        finally:
            self._set_codes([])

    def click(self, x, y, button='none', wheel=0):
        choices = {'none': 0, 'left': 1, 'right': 2, 'middle': 4}
        if button not in choices:
            raise ValueError('button must be none, left, right, or middle')
        self._validate_pointer(x, y, choices[button], wheel)
        try:
            self.pointer(x, y, choices[button], wheel)
        finally:
            self.pointer(x, y)

    def release_all(self):
        errors = []
        try:
            self._set_codes([])
        except Exception as exc:
            errors.append(str(exc))
        try:
            for button in range(1, 6):
                self._event(X.ButtonRelease, button)
            self.connection.sync()
        except Exception as exc:
            errors.append(str(exc))
        if errors:
            raise RuntimeError('; '.join(errors))
