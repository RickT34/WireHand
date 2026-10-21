# KVM MCP

通过 Python API、MCP 或网页控制台查看、操作被控设备。整个仓库只有一个安装包 `kvm-mcp`，代码统一放在 `src/kvm/`，按职责分目录：

| 目录 | 职责 |
| --- | --- |
| `core/` | 设备接口、通用帧分发、输入互斥与控制权 |
| `vision/` | 图像检测、目标匹配、分区与标记渲染 |
| `automation/` | Python 操作 API、快照与目标点击、录制和重放 |
| `platforms/` | ROCK 5B+ 硬件适配、离线图片后端、后端注册 |
| `interfaces/` | MCP 工具与网页的认证、路由、视频传输和前端 |
| `cli/`、`factory.py` | 命令行、配置读取与实例组装 |

自动化层通过 `core` 的接口使用后端，图像算法独立于设备和应用。只使用 Python API 时不会导入 MCP、网页或 ROCK 实现；安装依赖统一在根目录管理。依赖方向和扩展入口见 [架构说明](docs/ARCHITECTURE.md)。

## Python 自动化与操作录制

```python
from kvm import connect
from kvm.automation import Recording

with connect(backend="rock5b-plus") as computer:
    computer.click(320, 240)
    computer.hotkey("ctrl", "a")
    computer.write("hello")
    Recording.load("my-recording").replay(computer)
```

网页接管后点击“开始录制”，正常操作，再点“停止并生成脚本”。下载包包含可编辑的 `replay.py`、目标图像和结构化记录；脚本自动定位并等待目标，无需手动裁图或为每步编写等待循环。录制捕获经过网页控制台的输入；直接插在目标电脑上的物理键鼠需要额外的输入监听后端。

安装、录制工作流和当前能力边界见 [自动化与录制说明](docs/AUTOMATION.md)。

## 开发与验证

```sh
uv sync --frozen
uv run python -m unittest discover -s tests -t . -q
node --test tests/interfaces/test_*.cjs
uv build
```

测试在普通 Linux 开发机运行，不需要板卡；覆盖真实 stdio MCP、HTTP/WebSocket、控制权互斥、视频进程清理、HID 报告和包内 EDID。硬件性能、目标机键鼠行为和浏览器操作体验需要另做实机验收。

## 启动

在已经配置好硬件的板子上：

```sh
uv run kvm-mcp --backend rock5b-plus
```

网页配置好 `KVM_TOKEN` 和 `KVM_HOSTS` 后启动：

```sh
uv run kvm-web --backend rock5b-plus
```

也可以使用 `python -m kvm mcp --backend rock5b-plus` 和 `python -m kvm web --backend rock5b-plus`。必须通过 `--backend` 或 `KVM_BACKEND` 明确选择平台。

| 应用配置 | 默认值 | 含义 |
| --- | --- | --- |
| `KVM_BACKEND` | 无 | `rock5b-plus`、`image` 或 `module:factory` |
| `KVM_RUNTIME` / `--runtime` | `/run/kvm-mcp` | 同一设备的 SDK、MCP 与网页必须共用，包含输入锁和接管标记 |
| `KVM_TOKEN` | 无 | 网页密码，至少 20 个字符 |
| `KVM_BIND` | `0.0.0.0` | 网页监听地址 |
| `KVM_PORT` | `8080` | 网页端口 |
| `KVM_RECORDINGS` | `~/.local/share/kvm-mcp/recordings` | 网页录制的持久保存目录 |
| `KVM_HOSTS` | `localhost,127.0.0.1` | 允许访问的主机名/IP，不含端口，逗号分隔 |
| `KVM_TRUSTED_PROXIES` | 空 | 信任转发头的代理 IP/CIDR，逗号分隔；Nginx 同机时可设 `127.0.0.1` |

局域网地址、板卡名字和视频编码器路径都不再硬编码在网页应用里。系统服务、SSH MCP 示例和 ROCK 5B+ 参数见 [平台部署说明](docs/rock5b-plus/README.md)。

## 使用行为

MCP 提供 `status`、`screenshot`、`press_key`、`type_text`、`mouse`、`release_all`，以及视觉定位工具 `inspect_screen`、`inspect_region`、`click_target`。截图返回 JPEG；鼠标使用 0..1 归一化坐标；文本使用 US 键盘布局的 ASCII 字符。

视觉定位将同一张截图拆成总览和放大区域，每张区域图最多 18 个候选目标。超过 180 个候选时先返回九个大区域，agent 选区后再查看具体标记。agent 可直接用截图 ID 与目标 ID 点击；密集区域可分页，漏检位置可用 4×4 网格继续细分。点击前检查新画面和控制权变化。实现、接口和真实屏幕检查记录见 [视觉定位说明](docs/VISUAL_TARGETING.md)。

不连接板子也能调试真实截图，导出可交互的本地检查页面：

```sh
uv run kvm-inspect /path/to/screenshot.jpg --output artifacts/vision/my-screen
```

网页默认只查看。点击“接管控制”后浏览器独占输入，MCP 仍可查询状态与截图。连接断开或心跳超时后自动释放键鼠、结束接管并恢复 MCP，无响应连接最多约 7 秒后释放。释放失败也会结束接管并记录错误；服务重启会尝试重置旧会话并清理遗留标记。MCP 和网页应使用相同系统用户及运行目录。当前进程锁依赖 Linux/POSIX `flock`。

网页默认使用同源 H.264 WebSocket：HTTPS 页面走 WSS，HTTP 页面走 WS，可直接经过 Nginx 反代或 SSH TCP 隧道。浏览器优先通过 WebCodecs 立即解码绘制；不支持时自动改用 fMP4/MSE。平台仅提供编码帧，传输、封装、播放和观看者背压都在应用层。手动选择 WebRTC 可继续使用局域网 UDP 路径。

视频实际出帧后才关闭 JPEG 备用画面。队列积压时丢弃旧预测链并等待关键帧，客户端停止确认时关闭连接，避免不断累积延迟。ROCK 默认输出上限为 720p60、目标 2.5Mbps，可调整分辨率与码率。JPEG 备用路径仍限制为最高 5fps、约 5Mbps 图像负载。见 [远程视频与 Nginx SSL 配置](docs/REMOTE_VIEWING.md)。

无板卡也可启动带移动标记的本地 H.264 控制台，测试 WebCodecs/MSE、登录和 WebSocket（不会打开 HID）：

```sh
uv run python examples/video_transport_demo.py --image /path/to/screenshot.jpg
```

打开 `http://127.0.0.1:8800/`；密码写入 `/tmp/kvm-h264-demo-token`。省略 `--image` 使用合成背景。

## 继续开发

- [自动化库与录制脚本](docs/AUTOMATION.md)
- [架构、接口约定与新增平台](docs/ARCHITECTURE.md)
- [视觉定位与无硬件调试](docs/VISUAL_TARGETING.md)
- [ROCK 5B+ 安装与部署](docs/rock5b-plus/README.md)
- [ROCK 5B+ MPP 驱动部署](docs/rock5b-plus/MPP_DEPLOYMENT.md)
- [历史 WebRTC 实机验证](docs/rock5b-plus/WEBRTC.md)
- [重构前的原型记录](docs/history/PROTOTYPE.md)

2026-10-19 的帧率和驱动记录描述重构前的板端部署；不代表这次代码已重新部署或通过实机验收。
