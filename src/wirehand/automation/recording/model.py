"""Load and validate recordings before any replay input is sent."""
import json
from pathlib import Path

from PIL import Image

from wirehand.vision.matching import VisualTarget
from .schema import validate_step


def _safe_asset(directory, name):
    path = (directory/name).resolve()
    if not path.is_relative_to(directory.resolve()) or path.suffix.lower()!='.png':
        raise ValueError('Recording assets must be PNGs inside the recording directory')
    return path

class Recording:
    """录制目录中的步骤、视觉目标和资源。

    使用 load() 读取 recording.json 并验证所有目标图片与动作，再交给 Computer.replay。
    """
    def __init__(self, directory, data):
        self.directory,self.data = Path(directory),data
        self.steps = data['steps']

    @classmethod
    def load(cls, directory, *, allow_incomplete=False):
        """从本地目录读取并验证录制。

        Args:
            directory: 包含 recording.json 和 assets/ 的目录。
            allow_incomplete: 允许检查中断或失败的录制；默认 False，防止误执行不完整步骤。
        """
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
        """按 recording.json 中的目标 ID 返回 VisualTarget。"""
        value = self.data['targets'][identity]
        return VisualTarget(_safe_asset(self.directory,value['image']),tuple(value['point']),
                            tuple(value['screen_size']),strategy=value['strategy'])

    def replay(self, computer):
        """使用已有 Computer 会话执行语义步骤。"""
        computer.replay(self)
