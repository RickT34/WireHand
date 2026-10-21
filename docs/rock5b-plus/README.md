# ROCK 5B+ 平台

本目录保存 ROCK 5B+ 的接线、部署和实机记录。适配代码在 `packages/rock5b-plus/src/kvm_rock5b_plus/`，服务模板在 `deploy/rock5b-plus/`。以下命令针对重构后的包结构。

## 接线与系统环境

- 被控电脑 HDMI OUT → ROCK 5B+ HDMI IN。
- 被控电脑 USB 主机口 → 板子的全功能 USB-C 数据口。
- 板子的独立 USB-C PD 供电口继续连接电源。
- 首次使用单屏或复制模式，US 键盘布局，1080p60 输入。

已验证过的驱动组合与安装步骤见 [MPP_DEPLOYMENT.md](MPP_DEPLOYMENT.md)，安装结果见 [MPP_SETUP.md](MPP_SETUP.md)。这些是 2026-10-19 的历史证据。当前适配器保留 vendor MPP/RGA 与主线 GStreamer JPEG 两条实际硬件路径。

板子需要 `v4l-utils`、GStreamer 的 tools/base/good 插件以及 USB configfs 支持。MPP 模式额外需要带 Rockchip MPP/RGA 的内核和 FFmpeg；普通发行版 FFmpeg 不等同于该版本。

## 安装

把当前仓库内容放在板子的 `/opt/kvm-mcp`，在该目录执行：

```sh
uv sync --all-packages --frozen
```

没有 uv 时可以用标准 Python 包安装：

```sh
python3 -m venv /opt/kvm-mcp/.venv
/opt/kvm-mcp/.venv/bin/pip install /opt/kvm-mcp/packages/automation /opt/kvm-mcp/packages/rock5b-plus /opt/kvm-mcp
```

`requirements.txt` 仅是依赖导出，不能替代项目包安装。新命令、静态文件和 EDID 都通过项目包提供。

生成网页密码（只在配置尚不存在时执行）：

```sh
python3 - <<'PY'
from pathlib import Path
import secrets
path = Path('/etc/kvm-mcp.env')
with path.open('x') as stream:
    path.chmod(0o600)
    stream.write('KVM_TOKEN=' + secrets.token_urlsafe(24) + '\n')
    stream.write('KVM_HOSTS=192.168.50.152,r5p,localhost,127.0.0.1\n')
    stream.write('KVM_PORT=8080\nKVM_VIDEO_MODE=hardware\n')
PY
```

把示例中的 IP/主机名改为实际地址。应用默认仅接受 localhost 和 127.0.0.1；局域网访问必须配置 `KVM_HOSTS`。密码通过远端配置读取，不加入源码。

以 root 安装服务模板：

```sh
install -m 644 deploy/rock5b-plus/kvm-gadget.service /etc/systemd/system/
install -m 644 deploy/rock5b-plus/kvm-hdmi.service /etc/systemd/system/
install -m 644 deploy/rock5b-plus/kvm-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now kvm-gadget kvm-hdmi kvm-web
```

网页服务模板明确选择 `rock5b-plus`，并依赖该平台的 gadget 和 EDID 服务。平台初始化命令是 `rock5b-plus-gadget start|stop` 和 `rock5b-plus-hdmi [--restore]`；它们不需要导入网页或 MCP。

旧部署升级时，先停止 `kvm-web`，安装项目包、替换上述服务和 MCP 客户端配置，再重新加载并启动服务。旧根目录脚本不再使用；不要只覆盖几个 `.py` 文件后继续沿用旧 ExecStart。

## MCP

客户端可使用 [MCP 配置示例](../../deploy/rock5b-plus/mcp-config.json)：

```json
{
  "mcpServers": {
    "rock-kvm": {
      "command": "ssh",
      "args": ["-T", "-o", "BatchMode=yes", "r5p", "/opt/kvm-mcp/.venv/bin/kvm-mcp", "--backend", "rock5b-plus"]
    }
  }
}
```

SSH 提供认证，禁止分配终端。网页与 SSH MCP 默认共用 `/run/kvm-mcp`。若更改运行目录，两边都要明确指定；systemd 的 `EnvironmentFile` 不会自动传到 SSH 会话。

## 平台配置

这些变量只由 ROCK 适配器消费：

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `KVM_VIDEO_DEVICE` | 动态发现 | 覆盖 HDMI RX V4L2 节点 |
| `KVM_VIDEO_MODE` | `hardware` | `hardware` 或 `software` |
| `KVM_VIDEO_SOURCE` | 真实输入 | `test` 使用合成源，仍需要所选编码工具 |
| `KVM_TRANSPORT` | `h264` | vendor 输出 H.264，供应用选择 WS/WSS 或 WebRTC；`jpeg` 仅输出 JPEG |
| `KVM_MAX_FPS` | `60` | 1..60，不超过实际输入帧率 |
| `KVM_MAX_WIDTH` / `KVM_MAX_HEIGHT` | `1280` / `720` | 等比缩放输出上限；宽 320..3840，高 180..2160 |
| `KVM_H264_BITRATE` | `2500000` | 500000..20000000 bit/s |
| `KVM_FFMPEG` | `/usr/lib/jellyfin-ffmpeg/ffmpeg` | MPP/RGA FFmpeg 路径 |
| `KVM_EDID_FILE` | 包内 `edid/1080p-rgb.bin` | RGB EDID 配置 |

vendor 管线为 HDMI/V4L2 mmap → 上传硬件缓冲 → RGA → MPP H.264，并分出 5fps JPEG。主线硬件路径使用 GStreamer RGA/JPEG。MCP 优先读取两秒内的共享帧；网页未运行时可独立截图。

## 验收与维护

在开发机运行本地测试后，用控制机执行实机检查：

```sh
uv run python scripts/smoke_mcp.py r5p
uv run python scripts/smoke_web.py --origin http://192.168.50.152:8080 --frames 20 --live
uv run python scripts/smoke_h264.py --origin http://192.168.50.152:8080 --seconds 10
uv run python scripts/smoke_rtc.py --origin http://192.168.50.152:8080 --seconds 10
ssh r5p 'systemctl status kvm-web kvm-gadget kvm-hdmi --no-pager'
```

网页 smoke 脚本读取控制机仓库根目录的 `web-access.txt`（`Password: ...`，不入版本控制），只检查查看权限和视频，不发送实际键鼠输入。无信号负向检查使用 `smoke_mcp.py --disconnected`；仅在确认 HDMI/USB 都已断开时运行。JPEG smoke 不给 `--frames` 时验证无信号等待。

恢复原 EDID：先停止网页，停用 `kvm-hdmi`，执行 `/opt/kvm-mcp/.venv/bin/rock5b-plus-hdmi --restore`，再按需要启动网页。原始 EDID 保存在 `/var/lib/kvm-mcp/original-edid.bin`。恢复可能重新协商到当前管线不支持的格式。

浏览器连接断开后自动结束接管；无响应连接在约七秒后释放。正常停用网页也会清理接管状态；异常退出遗留的标记在服务重启时清理。`/run` 的状态在整机重启后清除。跨网络访问使用 [Nginx HTTPS/WSS 反代](../REMOTE_VIEWING.md)，网页视频与操作共用一个 HTTPS 端口。可选 WebRTC 使用 host ICE 和动态 UDP 端口，普通 HTTP 反代不转发其媒体流。

控制权释放的实机回归检查：

```sh
uv run python scripts/smoke_control.py --origin http://192.168.50.152:8080 --check-timeout
```

该检查会申请并释放浏览器控制权，验证正常断线与七秒超时。接管和断开会发送键鼠释放报告，不发送按键按下或鼠标点击；已有控制会话时直接退出。
