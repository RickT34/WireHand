"""ROCK 5B+ implementation of the application-facing platform contract."""
from contextlib import contextmanager, ExitStack
from pathlib import Path

from kvm_mcp.contracts import InputStatus
from .device import find_video
from .hid import HID, char_report, key_report, mouse_report, send_press, usb_state
from .snapshot import run, screenshot


class RockInput:
    def __init__(self, keyboard, pointer):
        self.keyboard = keyboard
        self.mouse = pointer
        self.position = (0, 0)

    def keys(self, keys: list[str]) -> None:
        self.keyboard.write(key_report(keys) if keys else bytes(8))

    def pointer(self, x, y, buttons=0, wheel=0) -> None:
        self.mouse.write(mouse_report(x, y, buttons, wheel))
        self.position = (x, y)

    def press_key(self, keys: list[str], hold_ms: int = 50) -> None:
        report = key_report(keys)
        if not 10 <= hold_ms <= 1000:
            raise ValueError('hold_ms must be 10..1000')
        send_press(self.keyboard, report, bytes(8), hold_ms / 1000)

    def type_text(self, text: str) -> None:
        if not 1 <= len(text) <= 1000:
            raise ValueError('Provide 1..1000 characters')
        reports = [char_report(char) for char in text]
        for report in reports:
            send_press(self.keyboard, report, bytes(8), .01)

    def click(self, x, y, button='none', wheel=0) -> None:
        buttons = {'none': 0, 'left': 1, 'right': 2, 'middle': 4}
        if button not in buttons:
            raise ValueError('button must be none, left, right, or middle')
        report = mouse_report(x, y, buttons[button], wheel)
        send_press(self.mouse, report, mouse_report(x, y))
        self.position = (x, y)

    def release_all(self) -> None:
        errors = []
        for device, report in ((self.keyboard, bytes(8)), (self.mouse, mouse_report(*self.position))):
            try:
                device.write(report)
            except (OSError, RuntimeError) as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError('; '.join(errors))


class Rock5BPlusBackend:
    name = 'rock5b-plus'

    def __init__(self, runtime: Path):
        self.runtime = Path(runtime)

    def input_state(self) -> InputStatus:
        state = usb_state()
        return {'connected': state == 'configured', 'detail': state}

    def status(self) -> dict:
        result = {'platform': self.name, 'input': self.input_state()}
        try:
            device = find_video()
            result['video'] = {'signal': True, 'device': device,
                               'timings': run(['v4l2-ctl', '-d', device, '--query-dv-timings'])}
        except (OSError, RuntimeError) as exc:
            result['video'] = {'signal': False, 'error': str(exc)}
        return result

    def screenshot(self, max_width: int = 1280) -> bytes:
        return screenshot(self.runtime, max_width)

    @contextmanager
    def open_input(self):
        with ExitStack() as stack:
            keyboard = stack.enter_context(HID('keyboard'))
            pointer = stack.enter_context(HID('mouse'))
            yield RockInput(keyboard, pointer)

    def create_video(self):
        from .video import VideoHub
        return VideoHub(self.runtime)
