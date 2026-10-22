"""电脑自动化 SDK：连接设备与快速开始。

在网页接管模式下录制操作，停止录制后脚本会保存在服务器的脚本库。
下载 ZIP、修改 `replay.py` 后，重新压缩并用相同文件名上传即可替换。
网页运行的是压缩包中的 `replay.py`，支持辅助 Python 模块和数据文件。
每次重复在新的 Python 进程中执行；工作目录是脚本所在文件夹。

```python
from wirehand import connect

with connect() as computer:
    computer.click(320, 180)
    computer.hotkey('ctrl', 'a')
    computer.write('hello')
    computer.press('enter')
```

坐标以原始截图像素为单位。视觉定位、等待和录制接口见
`wirehand.automation.computer.Computer`；可重用的图像目标见
`wirehand.vision.matching.VisualTarget`。

从下载的录制中取得目标并等待它出现：

```python
from pathlib import Path
from wirehand import connect
from wirehand.automation import Recording

flow = Recording.load(Path(__file__).parent)
with connect(timeout=15) as computer:
    computer.click(target=flow.target('T001'))  # 用 recording.json 中的实际目标 ID
```

脚本出现异常时停止后续重复；点击“停止执行”或切换接管模式会终止任务并释放键鼠。
脚本在服务器以服务用户身份执行，具有该用户的文件和进程权限。
SDK 文档由 pdoc 根据当前安装的代码自动生成。
"""
import os
from pathlib import Path


def connect(backend=None, runtime=None, **options):
    """创建自动化会话，可用作 `with` 上下文管理器。

    Args:
        backend: 平台名称、`module:factory` 或 Backend 实例；默认读取 KVM_BACKEND。
        runtime: 与网页共享的输入控制目录；默认 KVM_RUNTIME 或 /run/kvm-mcp。
        **options: 传给 Computer 的 timeout（默认 10 秒）和 poll_interval（默认 0.15 秒）。

    网页执行脚本时自动配置 backend 和 runtime，脚本通常直接调用 `connect()`。
    自行运行时设置环境变量，或使用 `connect(backend='docker-desktop')`。
    """
    from wirehand.automation import Computer
    from wirehand.platforms.registry import load_backend

    runtime = Path(runtime or os.environ.get('KVM_RUNTIME', '/run/kvm-mcp'))
    spec = backend if backend is not None else os.environ.get('KVM_BACKEND')
    if spec is None:
        raise ValueError('Set KVM_BACKEND or pass a backend name/object to connect')
    device = load_backend(spec, runtime) if isinstance(spec, str) else spec
    return Computer(device, runtime, **options)
