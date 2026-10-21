"""USB HID reports and bounded, serialized gadget access."""
import math
import os
from pathlib import Path
import select
import struct
import time

GADGET = Path('/sys/kernel/config/usb_gadget/kvm')
KEYBOARD_DESC = bytes.fromhex(
    '05010906a101050719e029e715002501750195088102950175088103'
    '9505750105081901290591029501750391039506750815002565'
    '0507190029658100c0'
)
# Buttons, absolute X/Y (0..32767), relative wheel. No report ID.
MOUSE_DESC = bytes.fromhex(
    '05010902a1010901a10005091901290315002501950375018102'
    '950175058103050109300931150026ff7f751095028102'
    '09381581257f750895018106c0c0'
)
MODIFIERS = {'ctrl': 1, 'shift': 2, 'alt': 4, 'meta': 8,
             'rightctrl': 16, 'rightshift': 32, 'rightalt': 64, 'rightmeta': 128}
KEYS = dict(zip('abcdefghijklmnopqrstuvwxyz', range(4, 30)))
KEYS.update(dict(zip('1234567890', range(30, 40))))
KEYS.update({'enter': 40, 'esc': 41, 'backspace': 42, 'tab': 43, 'space': 44,
             'capslock': 57, 'printscreen': 70, 'scrolllock': 71, 'pause': 72,
             'insert': 73, 'home': 74, 'pageup': 75, 'delete': 76, 'end': 77,
             'pagedown': 78, 'right': 79, 'left': 80, 'down': 81, 'up': 82})
KEYS.update({f'f{i}': 57 + i for i in range(1, 13)})
PUNCTUATION = {' ': 44, '-': 45, '=': 46, '[': 47, ']': 48, '\\': 49,
               ';': 51, "'": 52, '`': 53, ',': 54, '.': 55, '/': 56,
               '\n': 40, '\t': 43}
SHIFTED = dict(zip('!@#$%^&*()_+{}|:"~<>?', '1234567890-=[]\\;\'`,./'))


def char_report(char: str) -> bytes:
    modifier = 0
    if char in SHIFTED:
        modifier, char = 2, SHIFTED[char]
    elif 'A' <= char <= 'Z':
        modifier, char = 2, char.lower()
    code = KEYS.get(char, PUNCTUATION.get(char))
    if code is None:
        raise ValueError('type_text supports US-layout ASCII only; use the target IME for other languages')
    return bytes([modifier, 0, code, 0, 0, 0, 0, 0])


def key_report(keys: list[str]) -> bytes:
    modifier, codes = 0, []
    if not keys:
        raise ValueError('Provide at least one key')
    for key in keys:
        key = key.lower()
        if key in MODIFIERS:
            modifier |= MODIFIERS[key]
        elif key in KEYS or key in PUNCTUATION:
            code = KEYS.get(key, PUNCTUATION.get(key))
            if code not in codes:
                codes.append(code)
        else:
            raise ValueError(f'Unknown key: {key}')
    if len(codes) > 6:
        raise ValueError('At most six non-modifier keys may be pressed together')
    return bytes([modifier, 0] + codes + [0] * (6 - len(codes)))


def mouse_report(x: float, y: float, buttons: int = 0, wheel: int = 0) -> bytes:
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
        raise ValueError('x and y must be finite normalized coordinates in [0, 1]')
    if not 0 <= buttons <= 7 or not -127 <= wheel <= 127:
        raise ValueError('Invalid buttons or wheel')
    return struct.pack('<BHHb', buttons, round(x * 32767), round(y * 32767), wheel)


def usb_state() -> str:
    binding = GADGET / 'UDC'
    if not binding.exists() or not binding.read_text().strip():
        return 'gadget not configured'
    return (Path('/sys/class/udc') / binding.read_text().strip() / 'state').read_text().strip()


def hid_path(function: str) -> Path:
    major_minor = (GADGET / 'functions' / f'hid.{function}' / 'dev').read_text().strip()
    return Path('/dev/char') / major_minor


class HID:
    def __init__(self, function: str):
        self.function = function
        self.fd = None

    def __enter__(self):
        if usb_state() != 'configured':
            raise RuntimeError('USB host not connected/configured; connect the ROCK USB-C data port to the target')
        self.fd = os.open(hid_path(self.function), os.O_WRONLY | os.O_NONBLOCK)
        return self

    def write(self, report: bytes):
        deadline = time.monotonic() + 1
        while True:
            try:
                if os.write(self.fd, report) != len(report):
                    raise RuntimeError('Incomplete HID report')
                return
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('USB host did not consume HID report within one second')
                select.select([], [self.fd], [], remaining)

    def __exit__(self, *exc):
        os.close(self.fd)


def send_press(hid, report: bytes, release: bytes, duration: float = 0.03):
    try:
        hid.write(report)
        time.sleep(duration)
    finally:
        hid.write(release)
