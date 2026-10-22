# Python 自动化与操作录制

[返回文档导航](README.md)

## 安装与模块

整个项目使用一个安装包。Python API 位于 `wirehand.automation`，图像检测与匹配位于 `wirehand.vision`；网页与 MCP 使用同一套实现。模块之间按职责解耦，根目录统一管理安装和依赖。

```bash
uv sync --frozen
```

其他安装方式见 [贡献与本地开发](../CONTRIBUTING.md)。Docker 用户直接通过 `./deploy/docker-desktop/desktopctl shell` 进入已安装 SDK 的容器。

`wirehand.automation` 导出 `Computer`、`Recorder`、`Recording` 与中断异常；`wirehand.vision` 导出 `VisualTarget` 和匹配异常。`wirehand.connect()` 从参数或环境读取后端配置，延迟加载指定平台。直接构造 `Computer(backend, runtime)` 则完全由调用方注入后端。

SDK 使用 POSIX 文件锁，在后端所在环境运行：ROCK 脚本在板子上执行，Docker 桌面脚本在容器中执行。宿主机上的 `connect()` 不会自动连接远端或容器；自定义设备与远程传输可实现 `Backend` 接口。

## Python API

```python
from pathlib import Path
from wirehand import connect
from wirehand.automation import Recording

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
| `scroll(clicks,...)` | 在 target、像素 x/y 或屏幕相对位置 position=(x,y) 处滚动 |
| `drag(target=...,path=...)` | 从视觉目标起始，按记录的相对轨迹拖动并释放 |
| `move_path(path=...)` | 默认使用 0–1 的 x/y 路径；指定 target 时使用相对像素 dx/dy |
| `press` / `hotkey` / `write` | 按键、组合键与文本 |
| `key_sequence(events)` | 重放键盘按下/释放状态，保留组合键和输入法相关按键 |
| `sleep(seconds)` | 可取消的等待，保留步骤间隔 |
| `record(directory)` | 录制现有 Python 程序对该实例的操作 |
| `replay(recording)` | 执行结构化录制 |
| `cancel()` | 请求中断；拖动/按住的键在退出时尝试释放 |

视觉目标可以跟随位置移动；匹配也尝试参考屏幕比例及小范围缩放。默认等待超时 10 秒、轮询间隔 0.15 秒，可以在 `connect(..., timeout=..., poll_interval=...)` 或 `Computer(...)` 配置。`click(x,y)`、`move_to(x,y)` 等使用后端截图的像素坐标；`scroll(position=(x,y))` 和无 target 的 `move_path` 使用 0–1 屏幕相对坐标。浏览器显示缩放不改变这些坐标约定。

真实 HDMI 截图提取的局部模板、可复现的匹配代码，以及快照编号与持久 target 的区别，见 [视觉定位示例](VISUAL_TARGETING.md#录制脚本如何使用视觉-target)。

## 网页录制工作流

脚本会自动保存到服务器的脚本库。结束接管后，可以在网页直接运行并设置重复次数或无限重复，
也可下载修改 `replay.py`，再上传同名 ZIP 替换。实际执行修改后的 Python 文件。完整操作见
[网页控制台](WEB_CONSOLE.md)；在线接口文档在 `/docs/`，由 pdoc 自动生成。

1. 打开 WireHand 控制台并接管输入。
2. 填写脚本名称并点击“开始录制”。界面显示正在录制以及已完成步骤数。
3. 正常操作鼠标、键盘和组合键菜单。
4. 点击“停止并生成脚本”，脚本自动进入服务器脚本库。
5. 结束接管后从网页运行；需要修改时下载 ZIP，解压检查 `replay.py` 与目标图片。

下面是在已配置好设备的板子上直接运行下载脚本的例子：

```sh
KVM_BACKEND=rock5b-plus /opt/kvm-mcp/.venv/bin/python my-recording/replay.py
```

也可以直接读取录制数据：

```python
from wirehand import connect
from wirehand.automation import Recording

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
from wirehand import connect
from wirehand.automation import Recording

flow = Recording.load(Path(__file__).parent)
with connect() as computer:
    computer.click(target=flow.target('target_0001'), button='left')
    computer.sleep(0.5)  # 录制时的操作间隔，可按需调整
    computer.write(text='hello')
```

只有鼠标按下时才截取按下前的画面，从点击附近选择最小候选元素，保存局部图片和点击偏移作为 target。
未检测到元素时保存点击周围的小块图像，不再扩大到整屏，也不要求录制时必须得到唯一匹配。
拖动使用按下时的同一张图片作为起点目标，按相对轨迹重放；双击合并后只保留一个目标。

键盘输入、快捷键、鼠标移动和滚轮不截图、不生成 target。移动与滚轮保存 0–1 的屏幕相对位置，
重放时不进行视觉等待。鼠标路径和键盘按下/释放保留时序，步骤之间的间隔生成可编辑的 `computer.sleep(...)`。
悬停菜单通过原有移动位置与等待间隔打开；菜单若换到不同位置，需要调整移动路径或重新录制。

没有纹理或有多个相似元素也允许完成录制。重放点击时仍会检查匹配分数与歧义：不能确认目标时停止，
不会偷偷退回旧坐标点击。需要时可在下载的脚本中修改 target 或使用显式坐标。

双击归并成一个动作，同向滚轮突发合并；修饰键点击、单独按 Meta 等修饰键和长按时长均保留。
单次持键/指针停留超过 30 秒会报错。录制限制为 500 个步骤、64 MiB 已保存模板。

## 等待、失败与控制权

每个视觉操作会自动执行：截图 → 匹配 → 连续两帧位置稳定 → 获取输入锁 → 再次核对目标 → 发送输入。如果目标在最终核对时移动，继续观察；输入一旦开始，失败就退出，不自动重发。

匹配不仅检查分数，还比较第二个空间上不同的匹配位置。出现多个相似目标时停止，避免用“离旧坐标最近”掩盖歧义。目标消失、布局或文字变化过大时会超时，需要重新录制或修改目标。

SDK、MCP 和网页使用相同 `KVM_RUNTIME`。人工接管或其他进程输入改变 revision 时，已有自动化实例会停止；重新开始需要创建新实例。网页仍保持当前的断开自动释放、恢复自动控制行为。

视觉匹配判断目标是否出现；业务结果，例如最后一次“保存”是否成功，需要脚本补充应用特定检查。键盘焦点、剪贴板内容、IME 初始状态、页面动画，以及后端截图缓存的新鲜度都会影响重放；Ctrl+V 只记录快捷键，无法从 HDMI 读取目标剪贴板。跨鼠标操作持续按住普通字符键，以及拖动中途修改键盘状态，目前会明确拒绝录制。

## 不连接硬件验证

```sh
uv run python examples/record_replay_demo.py --output artifacts/automation/my-demo
uv run python -m unittest discover -s tests -t . -q
node --test tests/interfaces/test_*.cjs
```

演示程序使用内存中的画面和输入记录后端：录制时点击 `(100,90)`，重放时按钮移动后实际定位到 `(290,190)`，随后重放文本输入；不向真实电脑发送输入。导出实际的 `recording.json`、模板、`replay.py`、前后画面与 `result.json`。

测试覆盖网页录制/认证下载、生成脚本执行、目标移动、延迟出现、重复目标拒绝、悬停菜单、键盘批次、双击、拖动、修饰键、断线中断、人工介入、取消释放和路径校验。实机输入手感、截图缓存与快速菜单/动画的时序需要部署后单独验收。
