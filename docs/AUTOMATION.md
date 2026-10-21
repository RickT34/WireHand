# Python 自动化与操作录制

## 安装与模块

整个项目使用一个安装包。Python API 位于 `kvm.automation`，图像检测与匹配位于 `kvm.vision`；网页与 MCP 使用同一套实现。模块之间按职责解耦，根目录统一管理安装和依赖。

```sh
pip install .

# 开发环境
uv sync --frozen
uv build
```

`kvm.automation` 导出 `Computer`、`Recorder`、`Recording` 与中断异常；`kvm.vision` 导出 `VisualTarget` 和匹配异常。`kvm.connect()` 从参数或环境读取后端配置，延迟加载指定平台。直接构造 `Computer(backend, runtime)` 则完全由调用方注入后端。

当前控制端使用 POSIX 文件锁。ROCK 后端直接访问板上的设备，因此脚本在板上运行，也可以通过 SSH 启动。其他设备或远程传输可实现 `Backend` 接口。当前没有 Windows/macOS 原生鼠标键盘监听器。

## Python API

```python
from pathlib import Path
from kvm import connect
from kvm.automation import Recording
from kvm.vision import VisualTarget

with connect(backend="rock5b-plus") as computer:
    image = computer.screenshot()       # Pillow RGB 图像，最大采集宽度 3840
    image.save("screen.png")
    computer.click(320, 240)             # 截图像素坐标
    computer.hotkey("ctrl", "a")
    computer.write("hello")             # 当前 ROCK 适配器要求 US 布局 ASCII
    computer.press("enter")

    # 对录制目标的操作自动等待其出现并稳定，无需自己写轮询。
    flow = Recording.load(Path("my-recording"))
    computer.click(target=flow.target("target_0001"))
```

| 方法 | 行为 |
| --- | --- |
| `screenshot()` / `size()` | 返回当前图像 / 图像尺寸 |
| `locate(target)` | 单次匹配，返回位置、框、分数与比例 |
| `wait_for(target)` | 自动等待匹配位置连续两帧稳定 |
| `click(x,y)` / `click(target=...)` | 坐标点击或视觉目标点击；支持按钮、双击、修饰键、长按 |
| `move_to(x,y)` | 像素坐标移动 |
| `scroll(clicks,target=...)` | 在视觉目标位置滚轮；也可明确传 x/y |
| `drag(target=...,path=...)` | 从视觉目标起始，按记录的相对轨迹拖动并释放 |
| `move_path(...)` | 保留能打开菜单等的悬停轨迹 |
| `press` / `hotkey` / `write` | 按键、组合键与文本 |
| `key_sequence(events)` | 重放键盘按下/释放状态，保留组合键和输入法相关按键 |
| `record(directory)` | 录制现有 Python 程序对该实例的操作 |
| `replay(recording)` | 执行结构化录制 |
| `cancel()` | 请求中断；拖动/按住的键在退出时尝试释放 |

视觉目标可以跟随位置移动；匹配也尝试参考屏幕比例及小范围缩放。默认等待超时 10 秒、轮询间隔 0.15 秒，可以在 `connect(..., timeout=..., poll_interval=...)` 或 `Computer(...)` 配置。坐标均相对于后端返回的截图，不使用浏览器显示缩放后的坐标。

## 网页录制工作流

1. 打开 KVM 控制台并接管输入。
2. 点击“开始录制”。界面显示正在录制以及已完成步骤数。
3. 正常操作鼠标、键盘和组合键菜单。
4. 点击“停止并生成脚本”，下载 ZIP。
5. 解压后检查 `replay.py` 与目标图片，交还手动控制，再运行脚本。

```sh
KVM_BACKEND=rock5b-plus /opt/kvm-mcp/.venv/bin/python my-recording/replay.py
```

也可以直接读取录制数据：

```python
from kvm import connect
from kvm.automation import Recording

with connect("rock5b-plus") as computer:
    Recording.load("my-recording").replay(computer)
```

网页只在明确开始录制后保存操作。录制的是经过 KVM 网页发送的输入；直接接在被控电脑上的物理键鼠没有经过这个通道，无法仅靠 HDMI 判断完整输入事件。

文件默认保存到运行网页服务用户的 `~/.local/share/kvm-mcp/recordings`，可用 `KVM_RECORDINGS` 更改。目录权限 0700，记录和模板权限 0600；下载需要网页登录认证。录制包含按键/文本与界面内容，适合保存到用户自己的目录。

正常停止或明确交还控制会完成录制；断线、超时会保存为 `interrupted`。采集/写入失败保存为 `failed`。未完整结束的记录默认拒绝重放；`Recording.load(..., allow_incomplete=True)` 只用于用户检查后明确接受部分流程，不是默认行为。

## 自动生成的内容

```text
recording-id/
├── recording.json     版本、状态、步骤、目标图像与点击偏移
├── replay.py          可编辑的普通 Python 脚本
└── assets/
    ├── target_0001.png
    └── target_0002.png
```

示例生成代码：

```python
from pathlib import Path
from kvm import connect
from kvm.automation import Recording

flow = Recording.load(Path(__file__).parent)
with connect() as computer:
    computer.click(target=flow.target('target_0001'), button='left')
    computer.write(target=flow.target('target_0002'), text='hello')
```

它不是固定坐标的事件回放：鼠标按下前取得屏幕，优先选择点击附近的候选元素；必要时扩大上下文，直到图像能在录制画面中唯一匹配。保存图像及真实点击点在图像中的偏移，重放时重新匹配并恢复偏移。

无法得到局部唯一目标时，可能使用整屏视觉条件，目标元数据中的 `strategy` 会是 `screen`。这类目标对布局变化更敏感。没有足够图像细节时录制会失败，不会生成一个无条件旧坐标点击。

普通鼠标经过不改变界面时会省略；悬停打开菜单时保留移动。双击归并成一个动作，同向滚轮突发合并；拖动保留相对轨迹；键盘按下/释放合并为事件序列。修饰键点击保留修饰键状态，单独按 Meta 等修饰键也会记录。长按超过 300ms 会保存持续时间，单次持键/指针停留超过 30 秒会报错。录制限制为 500 个步骤、64 MiB 已保存模板。

## 等待、失败与控制权

每个视觉操作会自动执行：截图 → 匹配 → 连续两帧位置稳定 → 获取输入锁 → 再次核对目标 → 发送输入。如果目标在最终核对时移动，继续观察；输入一旦开始，失败就退出，不自动重发。

匹配不仅检查分数，还比较第二个空间上不同的匹配位置。出现多个相似目标时停止，避免用“离旧坐标最近”掩盖歧义。目标消失、布局或文字变化过大时会超时，需要重新录制或修改目标。

SDK、MCP 和网页使用相同 `KVM_RUNTIME`。人工接管或其他进程输入改变 revision 时，已有自动化实例会停止；重新开始需要创建新实例。网页仍保持当前的断开自动释放、恢复自动控制行为。

第一版自动推导的是目标定位条件，不是任意业务成功条件。比如最后一次“保存”是否真的成功，还需要应用特定检查。键盘焦点、剪贴板内容、IME 初始状态、页面动画，以及后端截图缓存的新鲜度都会影响重放；Ctrl+V 只记录快捷键，无法从 HDMI 读取目标剪贴板。跨鼠标操作持续按住普通字符键，以及拖动中途修改键盘状态，目前会明确拒绝录制。

## 不连接硬件验证

```sh
uv run python examples/record_replay_demo.py --output artifacts/automation/my-demo
uv run python -m unittest discover -s tests -t . -q
node --test tests/interfaces/test_frontend.cjs
```

演示程序使用内存中的画面和输入记录后端：录制时点击 `(100,90)`，重放时按钮移动后实际定位到 `(290,190)`，随后重放文本输入；不向真实电脑发送输入。导出实际的 `recording.json`、模板、`replay.py`、前后画面与 `result.json`。

测试覆盖网页录制/认证下载、生成脚本执行、目标移动、延迟出现、重复目标拒绝、悬停菜单、键盘批次、双击、拖动、修饰键、断线中断、人工介入、取消释放和路径校验。实机输入手感、截图缓存与快速菜单/动画的时序需要部署后单独验收。

## 验证记录与演示

2026-10-21 的首次录制验证在本地浏览器完成了登录、接管、录制点击与 h/i 按键、停止、下载和交还。按钮移位后，记录的点击点约 `(100.29,89.72)` 被重定位到 `(290.29,189.72)`。当时采用拆包结构，其旧脚本需更新为本页的新导入方式。

此前证据位于 `artifacts/automation/` 中的 `browser-recording.zip`、`standalone-verification.json`、`recorder-ui.jpg` 和 `demo-recording/`。这些产物是模拟设备验证，不是实机 USB 验收。

重现当前网页演示：

```sh
uv run python examples/web_recording_demo.py
```

访问 `http://127.0.0.1:8767/`，演示密码为 `recording-demo-local-token-2026`。它仅绑定回环地址，使用生成的模拟屏幕。命令行录制演示直接调用 SDK，不启动网页或 MCP。
