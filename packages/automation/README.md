# kvm-automation

独立的 Python 自动化库：截图、鼠标、键盘、视觉目标定位、操作录制和可编辑脚本生成。依赖 NumPy、OpenCV Headless 和 Pillow，不依赖 MCP、网页框架、aiortc 或具体硬件包。

从仓库安装核心库：

```sh
pip install ./packages/automation
```

设备适配器通过 `kvm_automation.backends` entry point 或 `module:factory` 加载；也可以直接把实现 `Backend` 的对象传给 `Computer`。当前控制端使用 POSIX 文件锁；随仓库提供的设备适配器为 ROCK 5B+，不包含 Windows/macOS 原生输入监听器。

```python
from kvm_automation import Computer, Recording

with Computer.connect(backend="rock5b-plus") as computer:
    computer.screenshot().save("screen.png")
    computer.click(320, 240)          # 截图中的像素坐标
    computer.hotkey("ctrl", "a")
    computer.write("hello")
    Recording.load("my-recording").replay(computer)
```

`Computer` 支持 `screenshot`、`size`、`locate`、`wait_for`、`click`、`move_to`、`scroll`、`drag`、`move_path`、`press`、`hotkey`、`write`、`key_sequence`、`record`、`replay` 和 `cancel`。

```python
with computer.record("recordings/demo"):
    computer.click(320, 240)
    computer.write("hello")
```

录制生成 `recording.json`、`assets/*.png` 和 `replay.py`。重放时根据图像寻找目标，自动等待连续两帧稳定，再在输入前重新核对。多处匹配、超时或人工接管会停止；实际输入失败不会自动重试。

视觉快照、分区标记与网格细分在 `kvm_automation.vision` 中，可以独立于 MCP 使用：

```python
from kvm_automation.vision.service import VisionService
from kvm_automation.ownership import InputGate

vision = VisionService(backend, InputGate(runtime))
result = vision.inspect()
# result.metadata / result.images
```

离线检查图片：`kvm-inspect screen.jpg --output inspection/`。

网页录制入口和完整边界说明见仓库的 `docs/AUTOMATION.md`；核心库不导入该网页应用。脚本应在有对应设备适配器的机器运行，或者由调用方提供远程 Backend。
