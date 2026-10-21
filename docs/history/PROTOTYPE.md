# ROCK 5B+ KVM MCP 原型（历史归档）

> 以下记录保留重构前的部署状态和旧命令。当前代码结构、启动入口和部署方式以 [项目 README](../../README.md) 和 [平台部署说明](../rock5b-plus/README.md) 为准。本文不是当前安装指南。

用板载 HDMI IN 采集被控电脑屏幕，用 USB gadget 模拟键盘与绝对坐标鼠标，通过 SSH stdio 提供 MCP。被控电脑无需安装代理。MCP 客户端运行在可以 `ssh r5p` 的机器上。

2026-10-19 最新部署：vendor 内核下已启用 **MPP H.264 + WebRTC**，真实 HDMI 完整传输与接收端解码实测约 **1080p60 / 7 Mbit/s**。网页直接使用 video 元素播放，保留 5fps JPEG 兼容画面和 MCP 截图。驱动安装记录见 [MPP_SETUP.md](../rock5b-plus/MPP_SETUP.md)，WebRTC 配置与证据见 [WEBRTC.md](../rock5b-plus/WEBRTC.md)。下文 VEPU121/DMA-BUF 性能记录来自切换前的主线内核阶段。

## 网页控制台

打开 **http://192.168.50.152:8080/**。本机 `web-access.txt` 保存访问密码（权限 0600，不入版本控制）；远端密码配置为 `/etc/kvm-mcp.env`。服务 `kvm-web.service` 和 `kvm-hdmi.service` 已启动并启用开机加载。当前 HTTP 服务用于受信任局域网，跨公网访问需另配 HTTPS 或 SSH 隧道。

页面支持实时画面、全屏、鼠标拖动/点击/滚轮、键盘按下/释放和组合键菜单。默认只查看，点击“接管控制”才会发送输入。一个浏览器独占控制期间，MCP 输入被后端阻止，MCP 截图仍可使用。切换窗口会释放按键；浏览器断开后释放 HID，MCP 继续暂停，直到明确点击“交还 MCP”。连接无响应的控制会话最长约 7 秒后释放。

当前 vendor 内核视频链路：

```text
HDMI RX → V4L2 mmap → 硬件缓冲上传 → RGA 颜色转换/缩放
        ├→ MPP H.264 硬件编码 → WebRTC → 浏览器 video 播放
        └→ 5fps MPP JPEG → MCP 截图 / 网页兼容画面
```

- 默认最高 1920×1080、60 fps，H.264 目标 6 Mbit/s、上限 8 Mbit/s、无 B 帧，每 30 帧一个 IDR。`KVM_MAX_FPS` 可设置 1..60，实际帧率不超过输入。
- WebRTC 直接封装硬件 H.264 数据包，不做软件二次编码。每个查看者最多缓存 4 帧；溢出后等待新的 IDR，避免发送残缺预测链。键鼠走独立 WebSocket。
- 当前 vendor 管线存在原始帧上传拷贝；RGA 到编码器使用硬件缓冲。主线内核阶段曾使用 HDMI → RGA 的 DMA-BUF 直接导入。
- 默认“自动 · WebRTC”；连接失败或选择“兼容 · JPEG”时使用 5fps 硬件 JPEG 画面，页面会明确标注兼容模式。
- 全局恢复旧式完整帧率 JPEG 可设置 `KVM_TRANSPORT=jpeg` 并重启；编码仍使用硬件。当前 vendor 内核不使用主线的 `v4l2jpegenc`。
- MCP 优先读取共享的最新帧，按请求缩小图片。网页服务关闭后，MCP 可继续独立截图。

实测结果（2026-10-19，同一板子，合成 SMPTE 测试图，BGR 1080p，180 帧，不含真实 HDMI 和浏览器显示）：

| 链路 | 总用时 | 吞吐 | CPU 时间 |
| --- | ---: | ---: | ---: |
| RGA + VEPU121 硬件 | 2.842 秒 | 63.3 fps | 1.270 秒 |
| 软件转换 + JPEG | 4.509 秒 | 39.9 fps | 4.498 秒 |

硬件链路在此工作负载下减少约 72% CPU 时间。完整测试源 → 硬件编码 → WebSocket → Python 解码路径接收 120 帧用时 4.020 秒，约 29.9 fps，平均 JPEG 大小 128,927 字节（该测试图约 31 Mbit/s）。这些是合成图吞吐结果，**不是实际键鼠到屏幕的端到端延迟**。测试图片保存在本机 `artifacts/hardware-test.jpg`，临时测试服务已停止。

验证：24 项 Python 测试、4 项前端事件测试、实际 HTTP 登录与 WebSocket 权限/视频传输、MCP 共享帧读取通过。内置浏览器打开局域网地址返回 `net::ERR_BLOCKED_BY_CLIENT`，因此浏览器布局和实际交互验收仍待完成。

### 实机颜色修复与 DMA 优化（2026-10-19 后续测试）

初次连接 Mac 后，输入协商为 4K30 YUV 4:4:4，但启动采集时报告过 BGR3；内核同时记录 AVI infoframe 解包失败。实际画面出现青绿/粉色错色。重新打开采集后枚举到 NV24，GStreamer 又报 `Device wants 1 planes`，而 RGA 不支持直接输入 NV24。

采用 RGB 输入协商规避当前驱动/格式组合的问题：`edid/1080p-rgb.bin` 基于 v4l2-ctl 的 HDMI 配置，首选 1080p60，关闭 CTA 的 YCbCr 4:4:4/4:2:2 支持位。`kvm-hdmi.service` 在视频服务前应用，保持内容不变时不重复触发热插拔。原 EDID 备份在远端 `/var/lib/kvm-mcp/original-edid.bin`。视频启动前检查真实枚举格式，并固定为已确认的 BGR，避免把不支持的 YUV 格式继续送入 RGB 链路。

RGB 配置应用后，实机采集颜色恢复。当时 Mac 选择 1080p30；后续 vendor 阶段已改为 1080p60，并完成真实 WebRTC 传输测试。

在同一真实 1080p30 HDMI 输入下，各采集 90 个源缓冲区：

| 硬件链路 | 用时 | 输出帧 | CPU 时间 | 单核 CPU 占比 |
| --- | ---: | ---: | ---: | ---: |
| mmap 采集后拷贝到 RGA | 3.310 秒 | 89 | 1.449 秒 | 43.8% |
| DMA-BUF 采集直接导入 RGA | 3.403 秒 | 90 | 0.793 秒 | 23.3% |

两条链路都使用 RGA + JPEG 硬件编码；后者减少约 45% CPU 时间。这是短时对比，非精确端到端延迟。最终完整真实 HDMI → 硬件编码 → WebSocket → Python 解码测试：120 帧 / 3.977 秒（约 30fps），平均 JPEG 67,716 字节。正式服务已采用 DMA-BUF，26 项 Python 测试和 4 项前端测试通过。

原 EDID 恢复流程（会重新协商显示模式）：

```sh
ssh r5p 'systemctl stop kvm-web'
ssh r5p 'systemctl disable --now kvm-hdmi'
ssh r5p '/opt/kvm-mcp/.venv/bin/python /opt/kvm-mcp/configure_hdmi.py --restore'
ssh r5p 'systemctl start kvm-web'
```

恢复原 EDID 可能再次协商为 NV24；当前网页会明确报格式不支持，而不是继续显示错误颜色。

### MPP H.264 驱动安装

已安装官方 `linux-image-vendor-rk35xx=26.8.3` 及匹配设备树，并重启进入 `6.1.115-vendor-rk35xx`。内核启用了 MPP、HDMI RX、RGA 和 USB HID，配套 `jellyfin-ffmpeg7` 提供 MPP/RGA 用户态组件。

真实 HDMI → RGA → `h264_rkmpp` 已完成编码验证；随后 WebRTC 完整接收端实测 613 帧 / 10.159 秒，约 60fps。详见 [MPP_SETUP.md](../rock5b-plus/MPP_SETUP.md) 和 [WEBRTC.md](../rock5b-plus/WEBRTC.md)。

## 接线

1. 被控电脑的 HDMI OUT → ROCK 5B+ 的 **HDMI IN**（标准尺寸接口，注意区分另外两个 HDMI OUT）。
2. 被控电脑的 USB 主机接口 → ROCK 5B+ 的 **全功能 USB-C 数据口**，使用支持数据传输的 USB-A/C 到 USB-C 线。
3. ROCK 5B+ 的 **独立 USB-C PD 供电口**继续连接电源；网口继续接局域网。
4. 首次测试将目标设为 1920×1080、60 Hz，使用复制屏幕或单屏，英文 US 键盘布局，并打开空白文本编辑器。

5B+ 的两个 USB-C 功能不同，数据口支持 OTG。见 [Radxa 官方产品资料](https://radxa.com/products/rock5/5bp/) 与 [产品说明](https://dl.radxa.com/rock5/5b%2B/docs/radxa_rock5bp_product_brief.pdf)。

## 主线内核阶段的部署与证据（2026-10-19，历史记录）

- 主机：`r5p` / `192.168.50.152`，Armbian 26.2.6 / Debian 13，内核 `6.18.28-current-rockchip64`。
- HDMI：`snps_hdmirx`，当前 `/dev/video3`，支持 Video Capture Multiplanar；通过设备名称动态查找，避免重启编号变化。
- USB：`fc000000.usb`，已绑定复合 HID gadget，生成 `/dev/hidg0` 与 `/dev/hidg1`；按 configfs 的设备号查找，不硬编码 hidg 编号。
- 程序：远端 `/opt/kvm-mcp`；虚拟环境 `/opt/kvm-mcp/.venv`。
- `kvm-gadget.service` 已启动、启用开机加载，并通过服务重启检查。尚未进行整机重启验证。
- MCP 进程由 SSH 会话按需启动；SSH 负责认证。网页服务监听 8080，使用独立密码登录。当前使用现有 `r5p` 的 root 登录配置。
- 当前真实 HDMI 为 1920×1080、30Hz、BGR3；USB 为 `configured`。初始未接线时的 640×480 是默认时序，不代表有输入信号。
- 已通过本地 HID 报告/输入校验/释放行为测试，以及真实 SSH MCP 初始化、工具枚举、状态查询、无信号截图错误测试。
- 真实 HDMI 截图与视频传输已验证，USB 已枚举；真实操作手感、60Hz、分辨率切换及断线重连仍需进一步验收。

安装依赖时使用临时代理 `192.168.50.1:7890`。新系统时钟原本停在 5 月，已按控制机时间手动校正。Armbian 软件源当时返回 502，因此仅更新 Debian 软件源索引完成依赖安装，未替换内核或关闭签名校验。

## 连接 MCP

将 `mcp-config.json` 中的服务器条目加入支持 stdio 的 MCP 客户端。客户端必须能无交互地执行 `ssh r5p`，且不能向 SSH 分配终端。

```json
{
  "mcpServers": {
    "rock-kvm": {
      "command": "ssh",
      "args": ["-T", "-o", "BatchMode=yes", "r5p", "/opt/kvm-mcp/.venv/bin/python", "/opt/kvm-mcp/server.py"]
    }
  }
}
```

工具：

| 工具 | 用法 |
| --- | --- |
| `status` | 查询 HDMI 实际时序、USB 枚举状态 |
| `screenshot` | 返回 JPEG，默认宽度上限 1280，保留宽高比 |
| `press_key` | 如 `keys=["ctrl","alt","delete"]`，自动释放 |
| `type_text` | 最多 1000 个 ASCII 字符，要求 US 键盘布局；换行会按 Enter |
| `mouse` | `x/y` 为 0..1 归一化绝对坐标，支持点击与滚轮，自动释放 |
| `release_all` | 释放键盘与鼠标按钮，鼠标移至左上角 |

## 复现部署

在板子上安装系统依赖（需要 root）：

```sh
apt-get install --no-install-recommends python3-venv v4l-utils gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good
```

在本项目目录同步代码，然后安装：

```sh
ssh r5p 'mkdir -p /opt/kvm-mcp'
scp hid.py gadget.py server.py requirements.txt kvm-gadget.service r5p:/opt/kvm-mcp/
scp video.py web_control.py web_server.py kvm-web.service r5p:/opt/kvm-mcp/
scp rtc_video.py r5p:/opt/kvm-mcp/
scp configure_hdmi.py kvm-hdmi.service r5p:/opt/kvm-mcp/
scp -r edid r5p:/opt/kvm-mcp/
scp -r static r5p:/opt/kvm-mcp/
ssh r5p 'python3 -m venv /opt/kvm-mcp/.venv'
ssh r5p 'env http_proxy=http://192.168.50.1:7890 https_proxy=http://192.168.50.1:7890 /opt/kvm-mcp/.venv/bin/pip install -r /opt/kvm-mcp/requirements.txt'
ssh r5p 'install -m 644 /opt/kvm-mcp/kvm-gadget.service /etc/systemd/system/kvm-gadget.service'
ssh r5p 'systemctl daemon-reload && systemctl enable --now kvm-gadget.service'
```

修改 HID 描述符后需重启 gadget 服务，目标电脑会重新枚举 USB。

首次启用网页前，在远端生成独立密码配置（保存后只需执行一次；不会覆盖已有配置）：

```sh
ssh r5p 'python3 -' <<'PY'
from pathlib import Path
import secrets
path = Path('/etc/kvm-mcp.env')
if not path.exists():
    with path.open('x') as stream:
        path.chmod(0o600)
        stream.write('KVM_TOKEN=' + secrets.token_urlsafe(24) + '\nKVM_PORT=8080\nKVM_VIDEO_MODE=hardware\n')
PY
ssh r5p 'install -m 644 /opt/kvm-mcp/kvm-web.service /etc/systemd/system/kvm-web.service'
ssh r5p 'install -m 644 /opt/kvm-mcp/kvm-hdmi.service /etc/systemd/system/kvm-hdmi.service'
ssh r5p 'systemctl daemon-reload && systemctl enable --now kvm-hdmi'
ssh r5p 'systemctl daemon-reload && systemctl enable --now kvm-web'
```

`KVM_HOSTS` 可设为逗号分隔的允许访问主机名/IP（不含端口）；默认允许当前局域网 IP、r5p、rock-5b-plus、localhost、127.0.0.1。IP 改变时同步更新。更新网页代码后执行 `ssh r5p 'systemctl restart kvm-web'`。

## 检查与停用

```sh
uv sync --frozen
uv run python -m unittest -v
node --test test_frontend.cjs
uv run python smoke_mcp.py
uv run python smoke_web.py
uv run python smoke_web.py --frames 120 --live
uv run python smoke_rtc.py --seconds 10
ssh r5p 'systemctl status kvm-web --no-pager'
ssh r5p 'systemctl status kvm-gadget --no-pager'
ssh r5p 'v4l2-ctl -d /dev/video3 --query-dv-timings'
ssh r5p 'cat /sys/class/udc/*/state'
```

只有 HDMI 与 USB 均未连接时使用 `uv run python smoke_mcp.py --disconnected` 检查无信号错误。`smoke_web.py` 默认测试无 HDMI 信号场景，读取本机 `web-access.txt`，不会发送实际 HID 输入。

停用并撤销开机加载：`ssh r5p 'systemctl disable --now kvm-gadget'`。这会解除本项目的 USB gadget 绑定并清理其 configfs 项目。

仅停用网页：`ssh r5p 'systemctl disable --now kvm-web'`。手动接管标记位于 `/run/kvm-mcp/human-control`；停用网页不会自动解除 MCP 暂停，需重新启动网页并明确交还控制。重启整机后 `/run` 中的临时状态会清除。

## 能力范围

适用于提供可采集 HDMI 输出、接受标准 USB HID 的电脑或设备。HDCP 加密画面、只有触摸输入的设备、未输出 HDMI 的设备不属于通用兼容范围。键盘使用 boot protocol，BIOS/UEFI 和具体操作系统的识别仍须实机验证；绝对鼠标不声明 boot mouse protocol，固件界面可能仅支持键盘。

当前提供实时网页画面和 MCP 按需截图。中文文本需要目标系统输入法配合。多屏桌面可能导致绝对鼠标坐标与捕获屏幕不一致，首次使用单屏或复制模式。没有音频、ATX 电源控制、虚拟磁盘或多目标切换。

HID 请求在进程间互斥；无 USB 主机时快速报错；MCP 每次按键/点击都会尝试释放，网页允许持续按住并在失焦/断线时释放。物理拔线后的释放无法保证送达目标。USB VID/PID 使用 Linux gadget 原型示例值，仅作为本机实验配置。
