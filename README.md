# KVM MCP

通过 MCP 和网页控制台查看、操作被控设备。项目分成两个部分：

| 部分 | 代码 | 职责 |
| --- | --- | --- |
| 平台硬件适配 | `src/kvm_mcp/platforms/` | 设备发现、视频采集与编码、截图、键鼠输入、平台初始化 |
| MCP 与网页应用 | `src/kvm_mcp/app/` | MCP 工具、网页、认证、控制权仲裁、JPEG/WebRTC 传输 |

目前的实机适配是 **ROCK 5B+**。应用层通过 `Backend`、`InputSession` 和 `VideoSource` 接口使用平台，不导入具体适配器；平台层不导入 MCP、Starlette 或 aiortc。`bootstrap.py` 负责选择后端并组装应用。共享接口、数据分发与进程锁不构成第三套业务系统。

本次重构不保留根目录脚本入口；安装 Python 包后使用下面的新命令。

## 开发与验证

```sh
uv sync --frozen
uv run python -m unittest discover -s tests -t . -q
node --test tests/app/test_frontend.cjs
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

也可以使用 `python -m kvm_mcp mcp --backend rock5b-plus` 和 `python -m kvm_mcp web --backend rock5b-plus`。必须通过 `--backend` 或 `KVM_BACKEND` 明确选择平台。

| 应用配置 | 默认值 | 含义 |
| --- | --- | --- |
| `KVM_BACKEND` | 无 | `rock5b-plus` 或 `module:factory` |
| `KVM_RUNTIME` / `--runtime` | `/run/kvm-mcp` | 同一设备的 MCP 与网页必须共用，包含输入锁和接管标记 |
| `KVM_TOKEN` | 无 | 网页密码，至少 20 个字符 |
| `KVM_BIND` | `0.0.0.0` | 网页监听地址 |
| `KVM_PORT` | `8080` | 网页端口 |
| `KVM_HOSTS` | `localhost,127.0.0.1` | 允许访问的主机名/IP，不含端口，逗号分隔 |

局域网地址、板卡名字和视频编码器路径都不再硬编码在网页应用里。系统服务、SSH MCP 示例和 ROCK 5B+ 参数见 [平台部署说明](docs/rock5b-plus/README.md)。

## 使用行为

MCP 提供 `status`、`screenshot`、`press_key`、`type_text`、`mouse`、`release_all`，以及视觉定位工具 `inspect_screen`、`inspect_region`、`click_target`。截图返回 JPEG；鼠标使用 0..1 归一化坐标；文本使用 US 键盘布局的 ASCII 字符。

视觉定位将同一张截图拆成总览和放大区域，每张区域图最多 18 个候选目标。超过 180 个候选时先返回九个大区域，agent 选区后再查看具体标记。agent 可直接用截图 ID 与目标 ID 点击；密集区域可分页，漏检位置可用 4×4 网格继续细分。点击前检查新画面和控制权变化。实现、接口和真实屏幕检查记录见 [视觉定位说明](docs/VISUAL_TARGETING.md)。

不连接板子也能调试真实截图，导出可交互的本地检查页面：

```sh
uv run kvm-inspect /path/to/screenshot.jpg --output artifacts/vision/my-screen
```

网页默认只查看。点击“接管控制”后浏览器独占输入，MCP 仍可查询状态与截图。连接断开或心跳超时后自动释放键鼠、结束接管并恢复 MCP，无响应连接最多约 7 秒后释放。释放失败也会结束接管并记录错误；服务重启会尝试重置旧会话并清理遗留标记。MCP 和网页应使用相同系统用户及运行目录。当前进程锁依赖 Linux/POSIX `flock`。

网页支持 JPEG 和可选的 H.264 WebRTC。平台提供编码后的帧，应用负责协商、RTP 封装和每个观看者的背压。平台仅实现 JPEG 时，网页自动提供 JPEG 路径。

通过 SSH 本地端口转发访问时，localhost/127.0.0.1 地址默认使用 JPEG。WebRTC 只有实际出帧后才替换备用画面，连接失败后保持 JPEG，直到手动重试。JPEG 按客户端拉取节奏限制为最高 5fps、约 5Mbps 图像负载预算，适合 10Mbps 左右的链路。见 [SSH 与远程画面](docs/REMOTE_VIEWING.md)。

## 继续开发

- [架构、接口约定与新增平台](docs/ARCHITECTURE.md)
- [视觉定位与无硬件调试](docs/VISUAL_TARGETING.md)
- [ROCK 5B+ 安装与部署](docs/rock5b-plus/README.md)
- [ROCK 5B+ MPP 驱动部署](docs/rock5b-plus/MPP_DEPLOYMENT.md)
- [历史 WebRTC 实机验证](docs/rock5b-plus/WEBRTC.md)
- [重构前的原型记录](docs/history/PROTOTYPE.md)

2026-10-19 的帧率和驱动记录描述重构前的板端部署；不代表这次代码已重新部署或通过实机验收。
