#!/usr/bin/env python3
"""Configure this project's composite keyboard/absolute mouse gadget only."""
import argparse
from pathlib import Path
import subprocess
from kvm.platforms.rock5b_plus.hid import GADGET, KEYBOARD_DESC, MOUSE_DESC


def write(path, value):
    path.write_bytes(value if isinstance(value, bytes) else str(value).encode())


def stop():
    if not GADGET.exists():
        return
    if (GADGET / 'UDC').read_text().strip():
        write(GADGET / 'UDC', '\n')
    for link in (GADGET / 'configs/c.1').glob('hid.*'):
        link.unlink()
    for path in [GADGET / 'configs/c.1/strings/0x409', GADGET / 'configs/c.1',
                 GADGET / 'functions/hid.keyboard', GADGET / 'functions/hid.mouse',
                 GADGET / 'strings/0x409', GADGET]:
        if path.exists():
            path.rmdir()


def start():
    subprocess.run(['modprobe', 'libcomposite'], check=True)
    subprocess.run(['modprobe', 'usb_f_hid'], check=True)
    if not Path('/sys/kernel/config/usb_gadget').exists():
        subprocess.run(['mount', '-t', 'configfs', 'none', '/sys/kernel/config'], check=True)
    if GADGET.exists() and (GADGET / 'UDC').read_text().strip():
        return
    controllers = list(Path('/sys/class/udc').iterdir())
    if len(controllers) != 1:
        raise RuntimeError(f'Expected one UDC, found {len(controllers)}')
    GADGET.mkdir(exist_ok=True)
    for name, value in {'idVendor': '0x1d6b', 'idProduct': '0x0104',
                        'bcdDevice': '0x0100', 'bcdUSB': '0x0200'}.items():
        write(GADGET / name, value)
    strings = GADGET / 'strings/0x409'
    strings.mkdir(exist_ok=True)
    for name, value in {'serialnumber': 'r5p-kvm-001', 'manufacturer': 'Local KVM',
                        'product': 'ROCK 5B+ KVM Keyboard and Mouse'}.items():
        write(strings / name, value)
    config = GADGET / 'configs/c.1'
    (config / 'strings/0x409').mkdir(parents=True, exist_ok=True)
    write(config / 'strings/0x409/configuration', 'Keyboard + absolute mouse')
    write(config / 'bmAttributes', '0xc0')  # Independently powered.
    write(config / 'MaxPower', '2')
    for name, desc, protocol, subclass, length in [
        ('keyboard', KEYBOARD_DESC, 1, 1, 8), ('mouse', MOUSE_DESC, 0, 0, 6)
    ]:
        function = GADGET / 'functions' / f'hid.{name}'
        function.mkdir(exist_ok=True)
        for field, value in {'protocol': protocol, 'subclass': subclass,
                             'report_length': length, 'report_desc': desc}.items():
            write(function / field, value)
        link = config / f'hid.{name}'
        if not link.is_symlink():
            link.symlink_to(function)
    write(GADGET / 'UDC', controllers[0].name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop'])
    args = parser.parse_args()
    start() if args.action == 'start' else stop()


if __name__ == '__main__':
    main()
