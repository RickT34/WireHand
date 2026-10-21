"""Apply a persistent RGB-only EDID; retain the board's original for rollback."""
import argparse
import os
from pathlib import Path
import subprocess
import time

from kvm.platforms.rock5b_plus.device import find_video

PROFILE = Path(__file__).parent / 'edid/1080p-rgb.bin'
BACKUP = Path('/var/lib/kvm-mcp/original-edid.bin')


def validate_edid(data):
    if len(data) < 128 or len(data) % 128 or data[:8] != bytes.fromhex('00ffffffffffff00'):
        raise ValueError('Invalid EDID header/size')
    if len(data) != (data[126] + 1) * 128:
        raise ValueError('EDID extension count does not match size')
    if any(sum(data[i:i+128]) % 256 for i in range(0, len(data), 128)):
        raise ValueError('Invalid EDID checksum')


def configure(restore=False):
    device = None
    for _ in range(30):
        try:
            device = find_video()
            break
        except RuntimeError:
            time.sleep(.3)
    if device is None:
        raise RuntimeError('HDMI RX device unavailable')
    current = subprocess.check_output(['v4l2-ctl', '-d', device, '--get-edid=pad=0,format=raw'])
    validate_edid(current)
    if not BACKUP.exists():
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        with BACKUP.open('xb') as stream:
            stream.write(current)
    path = BACKUP if restore else Path(os.environ.get('KVM_EDID_FILE', str(PROFILE)))
    desired = path.read_bytes()
    validate_edid(desired)
    if current != desired:
        subprocess.run(['v4l2-ctl', '-d', device,
                        f'--set-edid=pad=0,format=raw,file={path}'], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--restore', action='store_true')
    configure(parser.parse_args().restore)


if __name__ == '__main__':
    main()
