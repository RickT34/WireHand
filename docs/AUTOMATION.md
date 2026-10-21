# 独立 Python 自动化库与操作录制

## 包与依赖

`kvm-automation` 是核心库，包含 Python API、视觉定位、录制、目标匹配和重放。它可以单独安装，不依赖 MCP、Starlette、aiortc 或 ROCK 硬件包。`kvm-rock5b-plus` 是可选设备插件；`kvm-mcp` 是调用核心库的 MCP/网页应用。

```sh
# 只安装核心库
pip install ./packages/automation

# 在 ROCK 5B+ 上使用设备接口，不安装 MCP/网页
pip install ./packages/automation ./packages/rock5b-plus

# 开发整个 workspace
uv sync --all-packages --frozen
uv build --all-packages
```

三个 wheel 分别生成在 `dist/`。核心库导出 `Computer`、`Recorder`、`Recording`、`VisualTarget` 和匹配/中断异常。原来的视觉代码位于 `packages/automation/src/kvm_automation/vision/`，MCP 不再拥有这部分实现。

当前控制端使用 POSIX 文件锁。ROCK 插件直接访问板上的设备，因此脚本在板上运行；也可以通过 SSH 启动脚本。其他设备或远程传输可实现 `Backend` 接口。本轮没有添加 Windows/macOS 原生鼠标键盘监听器或远程 RPC 服务。

## Python API

```python
from pathlib import Path
from kvm_automation import Computer, Recording, VisualTarget

with Computer.connect(backend="rock5b-plus") as computer:
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

视觉目标可以跟随位置移动；匹配也尝试参考屏幕比例及小范围缩放。默认等待超时 10 秒、轮询间隔 0.15 秒，可以在 `Computer(..., timeout=..., poll_interval=...)` 配置。坐标均相对于后端返回的截图，不使用浏览器显示缩放后的坐标。

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
from kvm_automation import Computer, Recording

with Computer.connect("rock5b-plus") as computer:
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
from kvm_automation import Computer, Recording

flow = Recording.load(Path(__file__).parent)
with Computer.connect() as computer:
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
node --test tests/app/test_frontend.cjs
```

演示程序使用内存中的画面和输入记录后端：录制时点击 `(100,90)`，重放时按钮移动后实际定位到 `(290,190)`，随后重放文本输入；不向真实电脑发送输入。导出实际的 `recording.json`、模板、`replay.py`、前后画面与 `result.json`。

测试覆盖网页录制/认证下载、生成脚本执行、目标移动、延迟出现、重复目标拒绝、悬停菜单、键盘批次、双击、拖动、修饰键、断线中断、人工介入、取消释放和路径校验。实机输入手感、截图缓存与快速菜单/动画的时序需要部署后单独验收。

## 本轮验证产物（2026-10-21）

本地浏览器实际完成了登录、接管、开始录制、画面点击、h/i 键输入、停止、下载和交还控制。导出的两步脚本已在新建虚拟环境运行：该环境仅安装 `kvm-automation` 及其三个依赖，没有 MCP/网页框架/ROCK 插件。将演示按钮移位后，记录的点击点约 `(100.29,89.72)` 被重定位到 `(290.29,189.72)`，随后重放 h/i 按键。

证据保存在 `artifacts/automation/`：`browser-recording.zip`、`standalone-verification.json`、`recorder-ui.jpg`，以及 `demo-recording/` 的脚本和前后图像。这些目录被 Git 忽略，均是模拟设备验证，不是实机 USB 验收。

重现网页演示（需要安装 workspace 的应用包）：

```sh
uv run python examples/web_recording_demo.py
```

访问 `http://127.0.0.1:8767/`，演示密码为 `recording-demo-local-token-2026`。它仅绑定回环地址，使用生成的模拟屏幕，不连接任何真实输入设备。核心库演示仍可用前面的 `record_replay_demo.py` 在没有网页依赖的环境运行。
