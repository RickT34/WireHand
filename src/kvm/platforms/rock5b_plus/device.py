"""ROCK HDMI RX discovery, shared by capture and EDID setup."""
import os
from pathlib import Path


def find_video():
    if os.environ.get('KVM_VIDEO_DEVICE'):
        return os.environ['KVM_VIDEO_DEVICE']
    for name in Path('/sys/class/video4linux').glob('video*/name'):
        if 'hdmirx' in name.read_text().lower():
            return '/dev/' + name.parent.name
    raise RuntimeError('未找到 HDMI 输入设备')


